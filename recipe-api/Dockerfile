FROM python:3.15-slim

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY recipes.py server.py ./

# Recipes and ingredients.yaml are mounted here at runtime (cloned from the
# private cookbook-manager-recipes repo by an init container).
ENV RECIPES_DIR=/recipes PORT=8000
USER 65534
EXPOSE 8000
CMD ["python", "server.py"]
