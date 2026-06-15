FROM python:3.11-slim

WORKDIR /app

# Install common requirements first (layer caching)
COPY common_requirements/py3.11.txt common_requirements/py3.11.txt
RUN pip install --no-cache-dir -r common_requirements/py3.11.txt

# Note : following are taken care in common_requirements :
#    - common/services/cloud/gcp/requirements/py3.11.txt    :: common_requirements
#    - common/utils/requirements/py3.11.txt                 :: common_requirements

# Install worker-specific requirements
COPY workers/face_verification_worker/requirements/py3.11.txt requirements/py3.11.txt
RUN pip install --no-cache-dir -r requirements/py3.11.txt

# Copy only the common modules this worker needs
COPY common/__init__.py common/__init__.py
COPY common/services/ common/services/
COPY common/utils/ common/utils/

# Copy the worker code
COPY workers/face_verification_worker/ workers/face_verification_worker/

CMD ["python", "workers/face_verification_worker/main.py"]
