# Employee Attendance & Analytics API

This is a FastAPI backend built to handle employee shift tracking, attendance regularizations, and deep analytics. The entire API logic, including complex MongoDB aggregations, is contained within `app/main.py` as required.

## What it does
- **Attendance Tracking**: Allows employees to punch in and punch out. It automatically calculates work hours, checks for half-days, and logs overtime minutes (with a 30-minute threshold).
- **Strict Timezone Handling**: All date math and grace period rules (like the 10-minute late grace period) are strictly enforced in IST (UTC+05:30), but everything is safely stored in the database as naive UTC or epoch milliseconds to prevent timezone bugs.
- **Race Condition Safety**: Uses unique compound indexes and database-level `DuplicateKeyError` handling to guarantee that double-clicks don't result in duplicate punch-in records.
- **Aggregated Analytics**: The heavy lifting for the analytics endpoints (like the department trends and late leaderboards) is completely offloaded to MongoDB aggregation pipelines instead of relying on Python memory loops.

## How to run it locally

1. **Set up a virtual environment**
   ```bash
   python -m venv .venv
   # On Windows: .venv\Scripts\activate
   # On Mac/Linux: source .venv/bin/activate
   ```

2. **Install the dependencies**
   ```bash
   pip install -r requirements.txt
   ```

3. **Configure your Database**
   You need a MongoDB database (version 6.0 or newer). Create a `.env` file in the root directory and add your connection details:
   ```env
   MONGO_URI="mongodb+srv://<username>:<password>@your-cluster.mongodb.net/?retryWrites=true&w=majority"
   MONGO_DB="attendance_db"
   ```

4. **Start the server**
   ```bash
   uvicorn app.main:app --port 8000
   ```
   
Once the server is running, you can test all the endpoints by visiting the interactive Swagger UI at **http://localhost:8000/docs**.
