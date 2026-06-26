FROM docker.io/python:3.14

RUN groupadd -r snake && useradd -r -g snake snake

WORKDIR /home/snake

COPY Pipfile* ./
ENV PIP_NO_CACHE_DIR=false
RUN pip install --disable-pip-version-check --no-cache-dir pipenv && pipenv install --system --deploy

COPY cloud-init.py ./

USER snake

CMD [ "python", "./cloud-init.py" ]
