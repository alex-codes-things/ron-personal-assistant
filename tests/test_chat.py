from io import StringIO

from ron.ai import InferenceMetrics, InferenceResult
from ron.assistant import RonAssistant
from ron.chat import ChatService, ChatSettings, ConversationHistory
from ron.core import Coordinator, EventType
from ron.routing import PromptRouter
from ron.terminal import TerminalChat


class FakeOllamaClient:
    def __init__(self, chunks: tuple[str, ...] = ("Hello", " Alex!")) -> None:
        self.chunks = chunks
        self.requests: list[list[dict[str, str]]] = []

    def stream_chat(self, messages, *, on_token, **options):
        del options
        message_list = list(messages)
        self.requests.append(message_list)
        for chunk in self.chunks:
            on_token(chunk)
        text = "".join(self.chunks)
        return InferenceResult(
            model="test-model",
            text=text,
            done_reason="stop",
            metrics=InferenceMetrics(
                first_token_seconds=0.1,
                elapsed_seconds=0.2,
                total_duration_seconds=0.2,
                load_duration_seconds=0.0,
                prompt_tokens=20,
                output_tokens=4,
                tokens_per_second=20.0,
            ),
        )


def test_history_keeps_complete_recent_turns() -> None:
    history = ConversationHistory(
        ready_turn_limit=2, continuous_turn_limit=4, character_limit=1_000
    )
    history.record("one", "first")
    history.record("two", "second")
    history.record("three", "third")

    assert history.turn_count == 2
    assert history.messages()[0] == {"role": "user", "content": "two"}

    history.set_continuous(True)
    history.record("four", "fourth")
    history.record("five", "fifth")
    assert history.turn_count == 4


def test_normal_prompt_responds_without_start_chat_command() -> None:
    coordinator = Coordinator()
    fake_client = FakeOllamaClient()
    expressions: list[str] = []
    coordinator.subscribe(
        EventType.FACE_EXPRESSION,
        lambda event: expressions.append(str(event.payload["expression"])),
    )
    chat = ChatService(coordinator, client=fake_client, settings=ChatSettings())
    streamed: list[str] = []

    result = chat.respond("How are you?", on_token=streamed.append)

    assert result.text == "Hello Alex!"
    assert streamed == ["Hello", " Alex!"]
    assert chat.history.turn_count == 1
    assert expressions == ["thinking", "speaking", "idle"]
    assert fake_client.requests[0][-1] == {
        "role": "user",
        "content": "How are you?",
    }


def test_previous_turn_is_sent_as_context() -> None:
    coordinator = Coordinator()
    fake_client = FakeOllamaClient()
    chat = ChatService(coordinator, client=fake_client, settings=ChatSettings())

    chat.respond("My favourite colour is blue.")
    chat.respond("What is my favourite colour?")

    second_request = fake_client.requests[1]
    assert {"role": "user", "content": "My favourite colour is blue."} in second_request
    assert {"role": "assistant", "content": "Hello Alex!"} in second_request


def test_terminal_supports_continuous_mode_and_clean_shutdown() -> None:
    prompts = iter(["Start a chat", "Hello", "/status", "/quit"])
    output = StringIO()
    fake_client = FakeOllamaClient(("Hi!",))
    coordinator = Coordinator()
    chat = ChatService(coordinator, client=fake_client, settings=ChatSettings())
    assistant = RonAssistant(coordinator, chat, PromptRouter(fake_client))
    terminal = TerminalChat(
        assistant, input_reader=lambda prompt: next(prompts), output=output
    )

    assert terminal.run() == 0
    rendered = output.getvalue()
    assert "Continuous chat started" in rendered
    assert "Ron  · Understanding your request" in rendered
    assert "Ron  · Thinking about the reply" in rendered
    assert "Ron  › Hi!" in rendered
    assert "Mode: continuous chat; remembered turns: 1" in rendered
    assert "See you soon" in rendered
    assert "RON  ·  PERSONAL ASSISTANT" in rendered
    assert "Ron >" not in rendered


def test_terminal_strips_control_sequences_from_model_output() -> None:
    prompts = iter(["Hello", "/quit"])
    output = StringIO()
    fake_client = FakeOllamaClient(("Safe\x1b[31m text",))
    coordinator = Coordinator()
    chat = ChatService(coordinator, client=fake_client, settings=ChatSettings())
    assistant = RonAssistant(coordinator, chat, PromptRouter(fake_client))
    terminal = TerminalChat(
        assistant, input_reader=lambda prompt: next(prompts), output=output
    )

    terminal.run()

    assert "\x1b" not in output.getvalue()
    assert "Safe[31m text" in output.getvalue()


def test_terminal_health_command_uses_runtime_monitor() -> None:
    prompts = iter(["/health", "/quit"])
    output = StringIO()
    fake_client = FakeOllamaClient(("unused",))
    coordinator = Coordinator()
    chat = ChatService(coordinator, client=fake_client, settings=ChatSettings())
    assistant = RonAssistant(coordinator, chat, PromptRouter(fake_client))
    terminal = TerminalChat(
        assistant,
        input_reader=lambda prompt: next(prompts),
        output=output,
        health_provider=lambda: "Health READY: all essential systems ready.",
    )

    assert terminal.run() == 0
    assert "Ron  › Health READY: all essential systems ready." in output.getvalue()
    assert fake_client.requests == []


def test_memory_failure_does_not_turn_a_chat_reply_into_failure() -> None:
    class FailingMemory:
        def remember_conversation(self, user: str, assistant: str) -> None:
            del user, assistant
            raise OSError("simulated memory failure")

    coordinator = Coordinator()
    fake_client = FakeOllamaClient(("Still working",))
    chat = ChatService(coordinator, client=fake_client, settings=ChatSettings())
    assistant = RonAssistant(
        coordinator,
        chat,
        PromptRouter(fake_client),
        memory=FailingMemory(),  # type: ignore[arg-type]
    )

    response = assistant.respond("Hello Ron")

    assert response.text == "Still working"
    assert chat.history.turn_count == 1


def test_spoken_chat_adds_voice_specific_style_instruction() -> None:
    coordinator = Coordinator()
    fake_client = FakeOllamaClient(("Right away.",))
    chat = ChatService(coordinator, client=fake_client, settings=ChatSettings())

    chat.respond("How are you?", spoken=True)

    system = fake_client.requests[0][0]["content"]
    assert "request arrived by voice" in system
    assert "original British-style personal assistant" in system
    assert "complete response will remain visible in the terminal" in system



def test_spoken_generation_does_not_animate_mouth_before_audio() -> None:
    coordinator = Coordinator()
    fake_client = FakeOllamaClient(("Ready.",))
    expressions: list[str] = []
    coordinator.subscribe(
        EventType.FACE_EXPRESSION,
        lambda event: expressions.append(str(event.payload["expression"])),
    )
    chat = ChatService(coordinator, client=fake_client, settings=ChatSettings())

    chat.respond("Status?", spoken=True)

    assert expressions == ["thinking", "idle"]
