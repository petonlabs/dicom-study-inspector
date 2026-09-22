FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN python -m pip install --no-cache-dir -r requirements.txt

COPY . .

CMD ["sh", "-c", "python -m pytest -q && python make_preaccess_fixture.py --out review/preaccess_fixture --n 36 && python echoview_inspect_v2.py --echoview review/preaccess_fixture/MIMIC_ECHO_View_Classifications.synthetic.csv --record-list review/preaccess_fixture/echo-record-list.synthetic.csv --dicom-root review/preaccess_fixture/dicom --out review/preaccess_run --n 20 && python verify_preaccess_outputs.py --out review/preaccess_run"]
