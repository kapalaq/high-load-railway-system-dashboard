FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY main.py processing.py train_data_contained.json ./
CMD ["python", "-u", "main.py"]
