from datetime import datetime
from io import BytesIO
import json
import re
import PyPDF2
from flask import Flask, render_template, request, jsonify
import pdfplumber
import smartsheet


app = Flask(__name__)

SMARTSHEET_ACCESS_TOKEN = "cjPRDR9uWSZxDnX3eJys3sPzgoKJBCYTu92nP"
SHEET_ID = "7292351353409412"

def check_pis(file_stream):
    file_stream.seek(0)
    reader = PyPDF2.PdfReader(file_stream)
    text = ""
    for i in range(len(reader.pages)):
        text += reader.pages[i].extract_text()

    matches = {}
    pi = []
    pattern = re.compile(r"([A-Za-z\s]+?)\s*(?:Lead\s+Principal\s+Investigator|Principal\s+Investigator|Investigator)\s*\((\d+\.?\d*)%\)\s*Dept: Pharmacology\s*\(\d+\)")
    for match in pattern.finditer(text):
        name = match.group(1).strip()
        effort = match.group(2)
        pi.append({'PI': name, 'Effort': effort})
    matches["PI"] = pi

    return matches
    

def extract_proposal(file_stream):
    file_stream.seek(0)
    reader = PyPDF2.PdfReader(file_stream)
    text = ""
    for i in range(len(reader.pages)):
        text += reader.pages[i].extract_text()

    keywords = ["Proposal No", "Funding Agency", "Project Title"]
    matches = {}

    for keyword in keywords:
        pattern = rf"{re.escape(keyword + ':')}\s*(.*?)(?=\n|$)"
        match = re.search(pattern, text)
        if match:
            matches[keyword] = match.group(1).strip()
        else:
            matches[keyword] = ""
    
    match = re.search(r'Award Admin Dept:\s*(\d+)', text)
    if match:
        matches["Award Admin Dept"] = match.group(1)
    else:
        matches["Award Admin Dept"] = "" 

    return matches

def extract_tabs(file_stream):
    file_stream.seek(0)
    with pdfplumber.open(file_stream) as pdf:
        tables = []
        for i in range(len(pdf.pages)):
            tables += pdf.pages[i].extract_tables()
        
        data = None
        for table in tables:
            for row in table:
                if row and 'Initial/Current Budget Period' in row[0]:
                    data = row
                    break

        if not data:
            return {}

        result = {}
        types = ["Budget ", "Project "]
        lines = [line.strip() for line in data[0].split("\n") if line.strip()][1:]

        for line in lines:
            sections = re.split(r'(?<=\d)\s+(?=[A-Z])', line)
            if len(sections) == 2:
                for i in range(2):
                    key_value = sections[i].split(":")
                    if len(key_value) > 1:
                        key = types[i] + key_value[0].strip()
                        result[key] = key_value[1].strip()

        return result

@app.route('/')
def index():
    return render_template('index.html', message="", parsed_data={})

@app.route('/parse', methods=['POST'])
def parse_file():
    message = ""
    parsed_data = {}

    file = request.files.get('file')
    if not file or file.filename == '':
        message = "No selected file"
    elif not file.filename.lower().endswith('.pdf'):
        message = "Invalid file type. Please upload a PDF."
    else:
        file_stream = BytesIO(file.read())

        pi_data = check_pis(file_stream)
        if not pi_data['PI']:
            return render_template('index.html', message="No Pharmacology PIs found.", parsed_data={})
        proposal_data = extract_proposal(file_stream)
        tab_data = extract_tabs(file_stream)
        parsed_data = {**pi_data, **proposal_data, **tab_data}

    return render_template('index.html', message=message, parsed_data=parsed_data)

@app.route('/send_to_smartsheet', methods=['POST'])
def send_to_smartsheet():
    try:
        data = request.get_json()
        if not data:
            return jsonify({"status": "error", "message": "Invalid JSON received"}), 400
        
        print("Received Data From Textboxes:", data)

        client = smartsheet.Smartsheet(SMARTSHEET_ACCESS_TOKEN)
        rows = []

        pi_data = json.loads(data.get('pi', '[]').replace("'", '"'))

        for pi in pi_data:
            row = smartsheet.models.Row()
            row.to_top = True

            name = pi['PI']
            name_parts = name.split()
            if len(name_parts) > 1:
                last_name = name_parts[-1]
                first_and_middle_names = " ".join(name_parts[:-1])
                name = f"{last_name}, {first_and_middle_names}"
            name = name.strip()

            row.cells.append({'column_id': 8961866223275908, 'value': name})
            row.cells.append({'column_id': 3437919805329284, 'value': float(pi['Effort'].replace('%', '').strip())})
            row.cells.append({'column_id': 60220084801412, 'value': data['agency']})
            row.cells.append({'column_id': 2312019898486660, 'value': datetime.strptime(data['budgetStart'], '%m/%d/%Y').strftime('%Y-%m-%d')})
            row.cells.append({'column_id': 6936169963278212, 'value': datetime.strptime(data['budgetEnd'], '%m/%d/%Y').strftime('%Y-%m-%d')})
            row.cells.append({'column_id': 6815619525857156, 'value': datetime.strptime(data['projectStart'], '%m/%d/%Y').strftime('%Y-%m-%d')})
            row.cells.append({'column_id': 1306670429065092, 'value': datetime.strptime(data['projectEnd'], '%m/%d/%Y').strftime('%Y-%m-%d')})
            row.cells.append({'column_id': 1186119991644036, 'value': data['title']})
            row.cells.append({'column_id': 5689719619014532, 'value': data['propNo']})
            row.cells.append({'column_id': 7941519432699780, 'value': float(data['dept']) if data['dept'].isdigit() else data['dept']})
            row.cells.append({'column_id': 3558470242750340, 'value': float(data['direct'].replace('$', '').replace(',', '').strip())})
            row.cells.append({'column_id': 8062069870120836, 'value': float(data['faBase'].replace('$', '').replace(',', '').strip())})
            row.cells.append({'column_id': 743720475643780, 'value': float(data['faAmt'].replace('$', '').replace(',', '').strip())})
            row.cells.append({'column_id': 5247320103014276, 'value': float(data['total'].replace('$', '').replace(',', '').strip())})
            row.cells.append({'column_id': 2874969851907972, 'value': 'Pending'})

            rows.append(row)

        response = client.Sheets.add_rows(SHEET_ID, rows)

        if response.message == "SUCCESS":
            if len(rows) == 0:
                return jsonify({"status": "error", "message": 'Nothing sent.'}), 400
            return jsonify({"status": "success", "message": "Row added successfully."}), 200
        return jsonify({"status": "error", "message": response.message or 'Unknown error.'}), 400

    except smartsheet.exceptions.SmartsheetException as e:
        print("Smartsheet API error:", str(e))
        return jsonify({"status": "error", "message": f"Smartsheet API error: {str(e)}"}), 500
    except Exception as e:
        print("Internal Error:", str(e))
        return jsonify({"status": "error", "message": str(e)}), 500

    
    
if __name__ == '__main__':
    app.run(debug=True)
