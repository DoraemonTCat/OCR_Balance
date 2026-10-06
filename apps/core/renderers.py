"""JSON rendering, with an indented form for reading.

The API answers machines, so by default it sends the most compact JSON it can:
one line, no spaces. That is unreadable the moment a person opens the file to
check what came back, and these answers are long - a page of ledger is dozens of
rows of a dozen columns each.

``?pretty`` indents it instead. Nothing else changes: the same data, the same
keys, in the same order.
"""
from __future__ import annotations

from rest_framework.renderers import JSONRenderer


class PrettyJSONRenderer(JSONRenderer):
    """Indent the response when the request asks for ``?pretty``.

    Any value will do - ``?pretty``, ``?pretty=1``, ``?pretty=yes`` - because
    the only reason to type it is to read the answer, and a caller who has to
    look up which value counts as true has been failed by the parameter.

    Thai is written out as itself rather than as ``\\uXXXX`` escapes, which is
    the point of reading it.
    """

    def get_indent(self, accepted_media_type, renderer_context):
        request = (renderer_context or {}).get("request")
        if request is not None and "pretty" in request.query_params:
            return 2
        return super().get_indent(accepted_media_type, renderer_context)

    def render(self, data, accepted_media_type=None, renderer_context=None):
        indent = self.get_indent(accepted_media_type, renderer_context)
        if not indent:
            return super().render(data, accepted_media_type, renderer_context)

        # ensure_ascii is DRF's default and turns every Thai character into an
        # escape, which defeats the purpose here.
        import json

        return json.dumps(
            data, indent=indent, ensure_ascii=False, default=str
        ).encode("utf-8")
