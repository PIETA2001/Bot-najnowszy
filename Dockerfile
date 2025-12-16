# Używamy lekkiej wersji Pythona
FROM python:3.10-slim

# Ustawiamy katalog roboczy w kontenerze
WORKDIR /app

# Kopiujemy plik z wymaganiami
COPY requirements.txt .

# Instalujemy biblioteki
RUN pip install --no-cache-dir -r requirements.txt

# Kopiujemy resztę plików (Twój kod bota.py, credentials.json itp.)
COPY . .

# Otwieramy port (wymagane przez Cloud Run / Webhook)
EXPOSE 8080

# Ustawiamy zmienną środowiskową dla portu (domyślnie 8080 w Cloud Run)
ENV PORT 8080

# Komenda startowa
CMD ["python", "bota.py"]
