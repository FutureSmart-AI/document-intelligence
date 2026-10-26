FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE 1
ENV PYTHONUNBUFFERED 1
ENV OMP_NUM_THREADS=1
ENV MKL_NUM_THREADS=1

ARG MODE
ENV MODE=${MODE}

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends gcc

COPY backend/requirements.txt .
RUN pip install --upgrade pip && \
    pip install pip-tools && \
    pip install --no-cache-dir torch --index-url https://download.pytorch.org/whl/cpu && \
    pip install --no-cache-dir -r requirements.txt

COPY backend/ .

EXPOSE 8000
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "2"]


