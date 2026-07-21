FROM python:3.13-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt
COPY app.py ./
COPY static ./static
RUN mkdir -p /var/lib/smtp-console /var/lib/mail-console
EXPOSE 18430
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "18430"]
