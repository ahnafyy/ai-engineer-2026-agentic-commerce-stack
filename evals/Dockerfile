FROM python:3.13-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
# Default: run all suites with a report
CMD ["python", "run_evals.py", "--suite", "all", "--report"]
