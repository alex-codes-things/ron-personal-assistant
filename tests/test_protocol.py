from ron.display.protocol import JsonLineDecoder, ProtocolError, encode_message


def test_decoder_reassembles_split_messages() -> None:
    decoder = JsonLineDecoder()

    assert decoder.feed(b'{"type":"po') == []
    assert decoder.feed(b'ng"}\n') == [{"type": "pong"}]


def test_decoder_separates_combined_messages() -> None:
    decoder = JsonLineDecoder()

    assert decoder.feed(b'{"type":"one"}\n{"type":"two"}\n') == [
        {"type": "one"},
        {"type": "two"},
    ]


def test_encoder_rejects_nan() -> None:
    try:
        encode_message({"value": float("nan")})
    except ProtocolError:
        return
    raise AssertionError("NaN should not be accepted by the face protocol")


def test_decoder_rejects_malformed_json() -> None:
    decoder = JsonLineDecoder()
    try:
        decoder.feed(b"{not-json}\n")
    except ProtocolError:
        return
    raise AssertionError("Malformed JSON should not be accepted")


def test_decoder_rejects_non_object_json() -> None:
    decoder = JsonLineDecoder()
    try:
        decoder.feed(b"[1,2,3]\n")
    except ProtocolError:
        return
    raise AssertionError("Top-level arrays should not be accepted")


def test_decoder_rejects_oversized_unterminated_data() -> None:
    decoder = JsonLineDecoder()
    try:
        decoder.feed(b"x" * 8_193)
    except ProtocolError:
        return
    raise AssertionError("Oversized unterminated input should not be accepted")
