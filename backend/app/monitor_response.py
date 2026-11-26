"""Encode native monitoring trees once, preserving FastAPI scalar conversion."""
import json
from fastapi.encoders import jsonable_encoder
from starlette.responses import JSONResponse


class MonitorResponse(JSONResponse):
    def render(self, content):
        # The monitor builds string-keyed native dict/list trees. The stdlib
        # encoder walks these in C; only dates/other non-native leaf values
        # need FastAPI conversion. Avoid another Python walk over every node
        # diagnosis for every task in a large batch.
        return json.dumps(content, ensure_ascii=False, allow_nan=False,
                          separators=(',', ':'), default=jsonable_encoder).encode('utf-8')
