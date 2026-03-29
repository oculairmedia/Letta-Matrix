from src.matrix.streaming import LiveEditStreamingHandler


def test_live_edit_debounce_is_reduced() -> None:
    assert LiveEditStreamingHandler.EDIT_DEBOUNCE_S == 0.2
