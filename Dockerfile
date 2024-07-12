FROM python:3

RUN groupadd -r snake && useradd -r -g snake snake

WORKDIR /home/snake

COPY Pipfile* ./
RUN pip install --no-cache-dir pipenv && pipenv install --system --deploy

COPY cloud-init.py ./

USER snake

CMD [ "python", "./cloud-init.py" ]
