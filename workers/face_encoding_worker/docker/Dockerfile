FROM python:3.11-slim

ARG ENCODER=face_recognition

WORKDIR /app

# Install common requirements first (layer caching)
COPY common_requirements/py3.11.txt common_requirements/py3.11.txt
RUN pip install --no-cache-dir -r common_requirements/py3.11.txt

COPY common/services/encoding/requirements /tmp/common/services/encoding/requirements
# Install encoding service-specific requirements (layer caching)
RUN if [ "$ENCODER" = "face_recognition" ]; then \
        apt-get update && apt-get install -y \
            build-essential \
            cmake \
            g++ \
            git \
            && rm -rf /var/lib/apt/lists/* && \
        mkdir -p /app/common/services/encoding/requirements/face_recognition; \
        cp /tmp/common/services/encoding/requirements/face_recognition/py3.11.txt /app/common/services/encoding/requirements/face_recognition/; \
        pip install --no-cache-dir -r /app/common/services/encoding/requirements/face_recognition/py3.11.txt; \
    elif [ "$ENCODER" = "gta_fdetect" ]; then \
        mkdir -p /app/common/services/encoding/requirements/gta_fdetect; \
        cp /tmp/common/services/encoding/requirements/gta_fdetect/py3.11.txt /app/common/services/encoding/requirements/gta_fdetect/; \
        pip install --no-cache-dir -r /app/common/services/encoding/requirements/gta_fdetect/py3.11.txt; \
    else \
        echo "Error: Invalid ENCODER value '$ENCODER'. Expected 'face_recognition' or 'gta_fdetect'." >&2; \
        exit 1; \
    fi && rm -rf /tmp/common/services/encoding/requirements

# Note : following are taken care in common_requirements :
#    - common/services/cloud/gcp/requirements/py3.11.txt    :: common_requirements
#    - common/utils/requirements/py3.11.txt                 :: common_requirements

# Install worker-specific requirements
COPY workers/face_encoding_worker/requirements/py3.11.txt requirements/py3.11.txt
RUN pip install --no-cache-dir -r requirements/py3.11.txt

# Copy only the common modules this worker needs
COPY common/__init__.py common/__init__.py
COPY common/services/ common/services/
COPY common/utils/ common/utils/

# Copy the worker code
COPY workers/face_encoding_worker/ workers/face_encoding_worker/

CMD ["python", "workers/face_encoding_worker/main.py"]
