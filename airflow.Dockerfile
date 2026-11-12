FROM apache/airflow:2.9.2

# Seulement ce qui manque a l'image Airflow (requests et SQLAlchemy y sont deja,
# et il ne faut surtout pas ecraser sa version de SQLAlchemy).
RUN pip install --no-cache-dir feedparser==6.0.11 PyMySQL==1.1.1
