import pytest
from src.matrix.poll_handler import (
    parse_poll_command,
    is_poll_command,
    is_poll_results_command,
    is_poll_close_command,
    parse_poll_results_command,
    parse_poll_close_command,
    build_poll_start_event,
    ParsedPoll,
    format_poll_results,
    PollResults,
    POLL_KIND_DISCLOSED,
    POLL_KIND_UNDISCLOSED,
)


class TestParsePollCommand:
    def test_basic_poll(self):
        text = '/poll "What for lunch?" "Pizza" "Sushi" "Salad"'
        result = parse_poll_command(text)
        assert result is not None
        assert result.question == "What for lunch?"
        assert result.options == ["Pizza", "Sushi", "Salad"]
        assert result.kind == POLL_KIND_DISCLOSED

    def test_disclosed_poll(self):
        text = '/poll disclosed "Show votes?" "Yes" "No"'
        result = parse_poll_command(text)
        assert result is not None
        assert result.kind == POLL_KIND_DISCLOSED
        assert result.question == "Show votes?"
        assert result.options == ["Yes", "No"]

    def test_undisclosed_poll(self):
        text = '/poll undisclosed "Secret?" "A" "B" "C"'
        result = parse_poll_command(text)
        assert result is not None
        assert result.kind == POLL_KIND_UNDISCLOSED
        assert result.question == "Secret?"
        assert result.options == ["A", "B", "C"]

    def test_not_enough_options(self):
        text = '/poll "Question?" "Only one option"'
        result = parse_poll_command(text)
        assert result is None

    def test_not_poll_command(self):
        text = "Regular message"
        result = parse_poll_command(text)
        assert result is None

    def test_truncates_to_20_options(self):
        options = " ".join([f'"Opt{i}"' for i in range(25)])
        text = f'/poll "Many options?" {options}'
        result = parse_poll_command(text)
        assert result is not None
        assert len(result.options) == 20


class TestIsPollCommand:
    def test_is_poll(self):
        assert is_poll_command('/poll "Q?" "A" "B"') is True

    def test_not_poll(self):
        assert is_poll_command("Hello world") is False

    def test_poll_without_space(self):
        assert is_poll_command('/poll"Q?" "A" "B"') is False


class TestPollResultsCommand:
    def test_is_poll_results(self):
        assert is_poll_results_command("/poll-results $abc123") is True

    def test_not_poll_results(self):
        assert is_poll_results_command("/poll-close $abc") is False

    def test_parse_poll_results(self):
        result = parse_poll_results_command("/poll-results $abc123xyz")
        assert result == "$abc123xyz"

    def test_parse_poll_results_no_dollar(self):
        result = parse_poll_results_command("/poll-results abc123")
        assert result is None


class TestPollCloseCommand:
    def test_is_poll_close(self):
        assert is_poll_close_command("/poll-close $abc123") is True

    def test_not_poll_close(self):
        assert is_poll_close_command("/poll-results $abc") is False

    def test_parse_poll_close(self):
        result = parse_poll_close_command("/poll-close $xyz789")
        assert result == "$xyz789"


class TestBuildPollStartEvent:
    def test_build_basic_poll(self):
        poll = ParsedPoll(
            question="Test?",
            options=["Yes", "No"],
            kind=POLL_KIND_DISCLOSED,
            max_selections=1
        )
        event = build_poll_start_event(poll)
        
        assert "org.matrix.msc1767.text" in event
        assert "Test?" in event["org.matrix.msc1767.text"]
        
        poll_data = event["org.matrix.msc3381.poll.start"]
        assert poll_data["kind"] == POLL_KIND_DISCLOSED
        assert poll_data["max_selections"] == 1
        assert poll_data["question"]["org.matrix.msc1767.text"] == "Test?"
        assert len(poll_data["answers"]) == 2
        assert poll_data["answers"][0]["id"] == "opt_0"
        assert poll_data["answers"][0]["org.matrix.msc1767.text"] == "Yes"


class TestFormatPollResults:
    def test_format_results(self):
        results = PollResults(
            poll_event_id="$test",
            question="Lunch?",
            total_votes=10,
            results={"opt_0": 6, "opt_1": 4},
            option_labels={"opt_0": "Pizza", "opt_1": "Sushi"},
            voters={"opt_0": ["@a:x", "@b:x"], "opt_1": ["@c:x"]}
        )
        formatted = format_poll_results(results)
        
        assert "Lunch?" in formatted
        assert "Total votes: 10" in formatted
        assert "Pizza" in formatted
        assert "60.0%" in formatted
