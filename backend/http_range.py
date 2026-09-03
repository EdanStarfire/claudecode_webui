"""RFC 7233 single-range parsing and response building for byte-serving endpoints.

Supports only a single range per request (the first range of a comma-separated
list is used), matching Starlette's own FileResponse behavior.
"""

from fastapi import Request
from fastapi.responses import Response


def parse_range_header(range_header: str, file_size: int) -> tuple[int, int]:
    """Parse a `Range` header value into an inclusive (start, end) byte tuple.

    Supports `bytes=start-end`, `bytes=start-` (open-ended), and `bytes=-suffix`
    (suffix length) forms. Only the first range of a comma-separated list is
    parsed. Raises ValueError for malformed or unsatisfiable ranges.
    """
    if not range_header.startswith("bytes="):
        raise ValueError(f"Unsupported range unit: {range_header!r}")

    spec = range_header[len("bytes="):].split(",", 1)[0].strip()
    if "-" not in spec:
        raise ValueError(f"Malformed range spec: {spec!r}")

    start_str, end_str = spec.split("-", 1)
    start_str = start_str.strip()
    end_str = end_str.strip()

    if start_str == "" and end_str == "":
        raise ValueError("Malformed range spec: both bounds empty")

    if file_size == 0:
        raise ValueError("Range not satisfiable for empty file")

    if start_str == "":
        # Suffix range: last N bytes.
        suffix_length = int(end_str)
        if suffix_length <= 0:
            raise ValueError(f"Invalid suffix length: {suffix_length}")
        start = max(0, file_size - suffix_length)
        end = file_size - 1
    else:
        start = int(start_str)
        end = int(end_str) if end_str != "" else file_size - 1

    if start < 0 or end < start or start >= file_size:
        raise ValueError(f"Range not satisfiable: {spec!r} for file size {file_size}")

    end = min(end, file_size - 1)
    return start, end


def build_resource_response(
    request: Request,
    content: bytes,
    media_type: str,
    filename: str,
    disposition: str = "inline",
) -> Response:
    """Build a 200/206/416 response for a fully-buffered resource, honoring Range.

    - No `Range` header: 200 with the full body (unchanged existing behavior).
    - Valid `Range` header: 206 with the sliced body and Content-Range/Content-Length/
      Accept-Ranges headers.
    - Invalid/unsatisfiable `Range` header: 416 with `Content-Range: bytes */{size}`.
    """
    file_size = len(content)
    range_header = request.headers.get("range")
    base_headers = {
        "Content-Disposition": f'{disposition}; filename="{filename}"',
        "Accept-Ranges": "bytes",
    }

    if range_header is None:
        return Response(content=content, media_type=media_type, headers=base_headers)

    try:
        start, end = parse_range_header(range_header, file_size)
    except ValueError:
        return Response(
            content=b"",
            media_type=media_type,
            status_code=416,
            headers={"Content-Range": f"bytes */{file_size}"},
        )

    sliced = content[start : end + 1]
    headers = {
        **base_headers,
        "Content-Range": f"bytes {start}-{end}/{file_size}",
        "Content-Length": str(len(sliced)),
    }
    return Response(content=sliced, media_type=media_type, status_code=206, headers=headers)
