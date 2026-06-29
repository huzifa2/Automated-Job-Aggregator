FROM mcr.microsoft.com/playwright/python:v1.61.0-jammy

WORKDIR /app

COPY requirements.txt .
RUN pip install -r requirements.txt

COPY smartling_bot.py .

CMD ["python", "-u", "smartling_bot.py"]
