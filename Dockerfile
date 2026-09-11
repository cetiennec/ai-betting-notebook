FROM python:3.12-slim

WORKDIR /app
COPY . .

ENV NOTEBOOK_HOST=0.0.0.0

EXPOSE 8420
CMD ["python3", "app.py"]
