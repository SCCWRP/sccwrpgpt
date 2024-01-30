# Original simple skeleton for the application was developed by Paul with his own platform.openai.com account
# The server was running Debian 10, and python 3.8.8 was installed

# He used the openai API for Python, version 1.9.0

# I will be listing the verisons for other libraries that were installed on the server on which the original app was running 
#    this way we can specify for the docker container

# These version numbers may not necessarily remain the same
# Above the imports i am simply noting the version that was on the previous server


# PSL Imports
import os, json, uuid, time, traceback


# ----------------- Installed libs ----------------- #
# psycopg2 v2.9.3
import psycopg2 

# pandas v1.2.4 
import pandas as pd

# flask v1.1.2
from flask import Flask, request, send_from_directory, render_template, redirect, send_file, session, jsonify, url_for, g
from werkzeug.utils import secure_filename

# sqlalchemy v1.4.15
from sqlalchemy import create_engine

# openai v1.9.0
from openai import OpenAI, NotFoundError

# requests v2.25.1
import requests

# ----------------- Custom Imports ----------------- #
from utils.db import get_db_info, execute_function_call
from utils.api import get_query
from utils.json import CustomJSONEncoder




# -------------------------------- Main App -------------------------------- #

app = Flask(__name__)
app.secret_key = os.getenv("FLASK_APP_SECRET_KEY")
app.debug = True 

# ----- Globals ----- #


# set the database connection string, database, and type of database we are going to point our application at
@app.before_request
def before_request():
    
    # A simple check for debugging, making it easier to resolve potenntial issues
    assert str(os.environ.get("DB_PORT", "")).isdigit(), "DB_PORT environment variable must be an integer value"

    # use sqlalchemy eng only for the schema info
    g.eng = create_engine(
        f"postgresql://{os.environ.get('DB_USER','')}:{os.environ.get('DB_PASSWORD','')}@{os.environ.get('DB_HOST','')}:{os.environ.get('DB_PORT','')}/{os.environ.get('DB_NAME','')}"
    )
    
    # Set up the interaction with the chat GPT Assistants API
    if session.get('GPT_MODEL') is None:
        session['GPT_MODEL'] = os.environ.get("GPT_MODEL", "gpt-3.5-turbo-1106")
        # session['GPT_MODEL'] = os.environ.get("GPT_MODEL", "gpt-4-1106-preview")
        
    
    if session.get('OPENAI_API_KEY') is None:
        api_key = os.getenv("OPENAI_API_KEY")
        session['OPENAI_API_KEY'] = api_key
        assert api_key is not None, "OPENAI_API_KEY not defined in the environment variables"
        
    
    g.client = OpenAI(api_key=session.get('OPENAI_API_KEY'))
    
    
    if session.get('ASSISTANT_ID') is None:
        
        
        db_schema_info = get_db_info(g.eng)
        
        database_schema_string = "\n".join(
            [
                f"Table: {table['table_name']}\nColumns: {', '.join(table['column_names'])}"
                for table in db_schema_info
            ]
        )
    
        # The tool / function that generates the SQL Query
        tools = [
            {
                "type": "function",
                "function": {
                    "name": "ask_database",
                    "description": "Use this function to answer user questions about chemistry, fish, infauna, and invertebrates in the Southern California Bight. Input should be a fully formed SQL query.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "query": {
                                "type": "string",
                                "description": f"""
                                        SQL query extracting info to answer the user's question.
                                        SQL should be written using this database schema:
                                        {database_schema_string}
                                        The query should be returned in plain text, not in JSON.
                                        """,
                            }
                        },
                        "required": ["query"],
                    },
                }
            }
        ]
        assistant = g.client.beta.assistants.create(
            name="Data search",
            description="You are to receive database table and column information and generate SQL queries to retrieve data that the user asks for",
            instructions="Answer user questions by generating SQL queries against the Unified Database. You should also consider whether the tool output would give the user the data they want. They may send you further messages to refine the query.",
            model=session.get('GPT_MODEL'),
            tools=tools
        )
        session['ASSISTANT_ID'] = assistant.id
        

    if session.get('THREAD_ID') is None:
        thread = g.client.beta.threads.create()
        session['THREAD_ID'] = thread.id
        
    if session.get('SESSION_ID', None) is None:
        session['SESSION_ID'] = str(uuid.uuid4())
        
    if session.get('SESSION_DIR', None) is None:
        session['SESSION_DIR'] = os.path.join(os.getcwd(), 'files', session.get('SESSION_ID'))
        
    if session.get('CHAT_HISTORY_PATH', None) is None:
        session['CHAT_HISTORY_PATH'] = os.path.join(os.getcwd(), 'files', session.get('SESSION_DIR'), 'chat.json')
        
        
    if not os.path.exists(session.get('SESSION_DIR')):
        os.mkdir(session.get('SESSION_DIR'))
    
    

@app.teardown_request
def teardown_request(exception):
    
    if hasattr(g, 'dbconn'):
        g.dbconn.close()
        
    if hasattr(g, 'eng'):
        g.eng.dispose()
        

        

#  ---------------- Routes ---------------- #

@app.route("/", methods=['GET','POST'])
def chat():
    return render_template('search.jinja2')



@app.route("/update-model", methods=['POST'])
def update_model():
    try:
        data = request.json
        newmodel = data.get('model')
        session['GPT_MODEL'] = newmodel
        return jsonify(success=True, message=f'Model successfully updated to {newmodel}'), 200
    except Exception as e:
        print(traceback.format_exc())
        return jsonify(success = False, message='Error updating model', devmessage = str(e)), 500


@app.route("/update-api-key", methods=['POST'])
def update_key():
    try:
        data = request.json
        newkey = data.get('api_key')
        session['OPENAI_API_KEY'] = newkey
        return jsonify(success=True, message='API Key sucessfully updated', devmessage='API Key sucessfully updated'), 200
    except Exception as e:
        print(traceback.format_exc())
        return jsonify(success = False, message='Error updating api key', devmessage = str(e)), 500


@app.route('/submit', methods=['POST'])
def submit():
    try:
        # get info for the interaction with the chat gpt assistants api
        client = g.client
        THREAD_ID = session.get('THREAD_ID')
        ASSISTANT_ID = session.get('ASSISTANT_ID')
        
        # Get the user's question
        data = request.json
        QUESTION = data["question"]
        TIMESTAMP = int(time.time())
        
        if not os.path.exists(session.get('CHAT_HISTORY_PATH')):
            with open(session.get('CHAT_HISTORY_PATH'), 'w') as chatfile:
                json.dump([{"timestamp": TIMESTAMP, 'prompt': QUESTION}], chatfile)
        else:
            with open(session.get('CHAT_HISTORY_PATH'), 'r') as chatfile:
                chathist = json.load(chatfile)
                
            chathist.append({"timestamp": TIMESTAMP, 'prompt': QUESTION})
            
            # Track seen values for key 'prompt'
            # dont write duplicated prompts
            seen = set()
            unique_prompts = []

            for item in chathist:
                if item['prompt'] not in seen:
                    unique_prompts.append(item)
                    seen.add(item['prompt'])

            # sort chronologically
            unique_prompts.sort(key = lambda c: c.get('timestamp'))
            
            
            with open(session.get('CHAT_HISTORY_PATH'), 'w') as chatfile:
                json.dump(unique_prompts, chatfile)
            
                
        
        try:
            sql = get_query(client=client, assistant_id=ASSISTANT_ID, thread_id=THREAD_ID, user_prompt=QUESTION)
        except Exception as e:
            print("Exception occurred in get_query")
            print(e)
            return jsonify({'error': e})
            
        if sql != '':
            print('sql')
            print(sql)
            try:
                # Run the query
                qryresult = pd.read_sql(sql.replace('%', '%%'), g.eng)
                
                session['FILE_DOWNLOAD_NAME'] = f"{ str(secure_filename(sql))[:200] }.xlsx"
                session['EXCEL_PATH'] = os.path.join(os.getcwd(), 'files', session.get('SESSION_DIR'), session.get('FILE_DOWNLOAD_NAME'))
                with pd.ExcelWriter(session.get('EXCEL_PATH')) as writer:
                    qryresult.to_excel(writer, index = False)

                # Convert to dict and then to JSON using the custom encoder
                qryresult_json = json.dumps(qryresult.fillna('').to_dict('records'), cls=CustomJSONEncoder)
            
                return jsonify({'sql': sql, 'records': qryresult_json})
            
            except Exception as e:
                print("Exception occurred:")
                print(e)
                return jsonify({'sql': sql, 'error': str(e)})

        
        # In this case, the assistant decided not to run the function and generate SQL
        # Get all messages from the thread once run is complete
        messages = client.beta.threads.messages.list(thread_id=THREAD_ID)

        assistant_responses = [msg for msg in messages.data if msg.role == 'assistant']

        resp = ''
        if len(assistant_responses) > 0:
            msg = assistant_responses[0]
            # There should be only one response so we return on first iteration
            for c in msg.content:
                resp = c.text.value
                
                print('message output:')
                print(resp)

            
        return jsonify({'message': resp})
    except Exception as e:
        print("Unexpected error")
        print(e)
        print(traceback.format_exc())
        return jsonify({'error': f"{str(e)} - We are working on a fix for this. In the meantime, try clearing browsing data or using an incognito tab"})
        


@app.route('/download', methods=['GET','POST'])
def download():
    if session.get('EXCEL_PATH') is None:
        return 'bad request'
    if session.get('FILE_DOWNLOAD_NAME') is None:
        return 'bad request'
    
    return send_file(
        session.get('EXCEL_PATH'), 
        as_attachment=True, 
        mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet', 
        download_name=session.get('FILE_DOWNLOAD_NAME')
    )
    

@app.route('/chathist', methods=['GET','POST'])
def chathist():
    if session.get('CHAT_HISTORY_PATH') is None:
        return 'bad request'
    
    # initialize
    chatjson = []
    
    if os.path.exists(session.get('CHAT_HISTORY_PATH')):
        with open(session.get('CHAT_HISTORY_PATH'), 'r') as chathist:
            chatjson = json.load(chathist)
    
    return jsonify({"chathist": chatjson})
    
