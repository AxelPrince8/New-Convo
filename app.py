from flask import Flask, render_template, request, jsonify, send_from_directory
import threading, time, uuid, os, requests

app = Flask(__name__)
UPLOAD_FOLDER = 'uploads'
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

# running_jobs: job_id -> dict with thread and control flag
running_jobs = {}

# --------- Replace/modify this function with your real FB send logic ----------
def send_message_via_facebook(token: str, recipient_id: str, text: str) -> dict:
    """
    Example implementation using the Messenger Send API for a Facebook Page.
    This requires a PAGE_ACCESS_TOKEN and the recipient PSID.
    If you use a different method (Graph user token or scraping), replace this.
    """
    # Example: Messenger Send API endpoint
    url = f"https://graph.facebook.com/v17.0/me/messages"
    params = {'access_token': token}
    payload = {
        "recipient": {"id": recipient_id},
        "message": {"text": text}
    }
    try:
        r = requests.post(url, params=params, json=payload, timeout=10)
        return {"status_code": r.status_code, "json": r.json() if r.text else {}}
    except Exception as e:
        return {"error": str(e)}

# worker that sends messages repeatedly with delay until stopped
def worker_loop(job_id, token, recipient_id, delay_seconds, message_lines):
    try:
        idx = 0
        while True:
            job = running_jobs.get(job_id)
            if not job or not job.get("running"):
                print(f"Job {job_id} stopping (flag).")
                break

            # get message (support multiple lines or single)
            text = message_lines[idx % len(message_lines)].rstrip("\n")
            print(f"[{job_id}] Sending to {recipient_id}: {text[:80]}...")
            res = send_message_via_facebook(token, recipient_id, text)
            job['last_result'] = res
            idx += 1

            # sleep with early-exit checks
            slept = 0.0
            step = 0.5
            while slept < delay_seconds:
                time.sleep(step)
                slept += step
                if not running_jobs.get(job_id) or not running_jobs[job_id].get("running"):
                    print(f"Job {job_id} stopping during sleep.")
                    return
    except Exception as ex:
        print(f"Worker {job_id} exception: {ex}")
    finally:
        # ensure cleanup
        if job_id in running_jobs:
            running_jobs.pop(job_id, None)
        print(f"Worker {job_id} finished.")

# ---- Routes ----
@app.route('/')
def index():
    return render_template('index.html')

@app.route('/start', methods=['POST'])
def start_job():
    data = request.form
    token = data.get('token', '').strip()
    recipient_id = data.get('recipient_id', '').strip()
    target_name = data.get('target_name', '').strip()
    delay = float(data.get('delay', '5'))
    # uploaded file
    file = request.files.get('message_file')
    if not token or not recipient_id:
        return jsonify({"error": "token and recipient_id required"}), 400

    if not file:
        return jsonify({"error": "Please upload a .txt message file"}), 400

    filename = f"{uuid.uuid4().hex}.txt"
    filepath = os.path.join(UPLOAD_FOLDER, filename)
    file.save(filepath)

    # read messages (each line is one message; blank lines ignored)
    with open(filepath, 'r', encoding='utf-8') as f:
        lines = [ln for ln in (l.strip() for l in f.readlines()) if ln]

    if not lines:
        return jsonify({"error": "Uploaded file is empty or only blank lines"}), 400

    # create a short unique id
    job_id = uuid.uuid4().hex[:8]

    # create and store job
    job_info = {
        "token": token,
        "recipient_id": recipient_id,
        "target_name": target_name,
        "delay": delay,
        "message_lines": lines,
        "running": True,
        "last_result": None,
        "filename": filename
    }
    running_jobs[job_id] = job_info

    # start thread
    t = threading.Thread(target=worker_loop, args=(job_id, token, recipient_id, delay, lines), daemon=True)
    job_info['thread'] = t
    t.start()

    return jsonify({"job_id": job_id, "message_count": len(lines)})

@app.route('/stop', methods=['POST'])
def stop_job():
    data = request.json or request.form
    job_id = data.get('job_id')
    if not job_id:
        return jsonify({"error": "job_id required"}), 400
    job = running_jobs.get(job_id)
    if not job:
        return jsonify({"error": "no active job with that id"}), 404
    job['running'] = False
    # worker thread will cleanup and remove itself; we can also pop after small wait
    return jsonify({"stopped": job_id})

@app.route('/status/<job_id>')
def status(job_id):
    job = running_jobs.get(job_id)
    if not job:
        return jsonify({"status": "not_found"}), 404
    return jsonify({
        "job_id": job_id,
        "running": job.get("running", False),
        "target_name": job.get("target_name"),
        "recipient_id": job.get("recipient_id"),
        "delay": job.get("delay"),
        "message_count": len(job.get("message_lines", [])),
        "last_result": job.get("last_result")
    })

@app.route('/uploads/<path:filename>')
def uploaded_file(filename):
    return send_from_directory(UPLOAD_FOLDER, filename, as_attachment=True)

if __name__ == "__main__":
    app.run(debug=True, port=5000)
