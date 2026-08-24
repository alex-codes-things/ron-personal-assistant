from pathlib import Path

from ron.ai import InferenceMetrics, InferenceResult
from ron.assistant import RonAssistant
from ron.chat import ChatService, ChatSettings
from ron.core import Coordinator
from ron.memory import AutoLearnMode, MemoryIntelligence, MemoryKind, MemoryPolicy, MemoryService
from ron.routing import PromptRouter, RouteSource
from ron.storage import StorageManager


class FakeOllamaClient:
    def __init__(self, reply: str = "Normal reply") -> None:
        self.reply = reply
        self.requests: list[list[dict[str, str]]] = []

    def stream_chat(self, messages, *, on_token, **options):
        del options
        self.requests.append(list(messages))
        on_token(self.reply)
        return InferenceResult(
            model="test-model",
            text=self.reply,
            done_reason="stop",
            metrics=InferenceMetrics(
                first_token_seconds=0.01,
                elapsed_seconds=0.02,
                total_duration_seconds=0.02,
                load_duration_seconds=0.0,
                prompt_tokens=10,
                output_tokens=2,
                tokens_per_second=100.0,
            ),
        )


def _memory(tmp_path: Path, *, online: bool = True) -> tuple[MemoryService, Path]:
    project = tmp_path / "project"
    external = tmp_path / "external"
    project.mkdir()
    external.mkdir()
    storage = StorageManager(project, locator=(lambda: external) if online else (lambda: None))
    storage.refresh_once()
    return MemoryService(project, storage), external


def test_explicit_remember_and_recall_work_without_llm(tmp_path: Path) -> None:
    memory, _external = _memory(tmp_path)
    coordinator = Coordinator()
    fake = FakeOllamaClient()
    chat = ChatService(coordinator, client=fake, settings=ChatSettings())
    assistant = RonAssistant(
        coordinator,
        chat,
        PromptRouter(fake),
        memory=memory,
    )

    remembered = assistant.respond("Remember that my amp is a Fender Mustang LT25")
    recalled = assistant.respond("What do you remember about my amp?")

    assert remembered.route.source is RouteSource.DETERMINISTIC
    assert "remember" in remembered.text.casefold()
    assert "Fender Mustang LT25" in recalled.text
    assert fake.requests == []


def test_duplicate_explicit_memory_is_not_stored_twice(tmp_path: Path) -> None:
    memory, _external = _memory(tmp_path)
    intelligence = MemoryIntelligence(memory)

    intelligence.handle("Remember that I prefer dark themes")
    result = intelligence.handle("Remember that I prefer dark themes")

    assert result is not None and "already remember" in result
    assert memory.catalog.counts()[0] == 1


def test_conservative_learning_saves_stable_fact_not_every_turn(tmp_path: Path) -> None:
    memory, _external = _memory(tmp_path)
    memory.policy = MemoryPolicy(AutoLearnMode.CONSERVATIVE)

    assert memory.consider_user_statement("Hello Ron, how are you?") is None
    learned = memory.consider_user_statement(
        "My electric guitar is a Squier Stratocaster, what settings should I use?"
    )

    assert learned is not None
    assert learned.metadata["source"] == "learned"
    assert "Squier Stratocaster" in learned.summary
    assert memory.catalog.counts()[0] == 1


def test_conservative_learning_rejects_temporary_and_secret_content(tmp_path: Path) -> None:
    memory, _external = _memory(tmp_path)
    memory.policy = MemoryPolicy(AutoLearnMode.CONSERVATIVE)

    assert memory.consider_user_statement("I prefer coffee today") is None
    assert memory.consider_user_statement("My password is swordfish") is None
    assert memory.catalog.counts()[0] == 0


def test_explicit_memory_refuses_credentials(tmp_path: Path) -> None:
    memory, _external = _memory(tmp_path)
    intelligence = MemoryIntelligence(memory)

    response = intelligence.handle("Remember that my API key is abc123")

    assert response is not None
    assert "won't store" in response
    assert memory.catalog.counts()[0] == 0


def test_context_retrieval_uses_relevant_durable_memory_only(tmp_path: Path) -> None:
    memory, _external = _memory(tmp_path)
    memory.remember_unique(
        MemoryKind.KNOWLEDGE,
        "My amp is a Fender Mustang LT25.",
        importance=90,
        metadata={"source": "explicit"},
    )
    memory.remember_conversation("Hello", "Hi")

    context = memory.context_for_prompt("What settings should I use on my amp?")
    generic = memory.context_for_prompt("Hello")

    assert "Fender Mustang LT25" in context
    assert "User: Hello" not in context
    assert generic == ""


def test_offline_recall_falls_back_to_local_summary(tmp_path: Path) -> None:
    memory, external = _memory(tmp_path)
    record, _created = memory.remember_explicit("My amp is a Fender Mustang LT25")
    assert (external / Path(record.relative_path)).exists()

    offline = StorageManager(memory.storage.project_root, locator=lambda: None)
    offline.refresh_once()
    offline_memory = MemoryService(memory.storage.project_root, offline)
    recalled = offline_memory.recall("amp")

    assert recalled
    assert recalled[0].content == "My amp is a Fender Mustang LT25"
    assert recalled[0].full_content_available is False


def test_forget_requires_confirmation_and_deletes_memory(tmp_path: Path) -> None:
    memory, external = _memory(tmp_path)
    record, _created = memory.remember_explicit("My amp is a Fender Mustang LT25")
    intelligence = MemoryIntelligence(memory)

    prompt = intelligence.handle("Forget about Fender Mustang")
    assert prompt is not None and "permanent" in prompt.casefold()
    assert memory.catalog.get_memory(record.memory_id) is not None

    result = intelligence.handle("yes, forget it")

    assert result is not None and "permanently forgot" in result
    assert memory.catalog.get_memory(record.memory_id) is None
    assert not (external / Path(record.relative_path)).exists()


def test_unrelated_prompt_cancels_pending_forget_instead_of_hijacking(tmp_path: Path) -> None:
    memory, _external = _memory(tmp_path)
    record, _created = memory.remember_explicit("My amp is a Fender Mustang LT25")
    intelligence = MemoryIntelligence(memory)

    assert intelligence.handle("Forget about Fender Mustang") is not None
    assert intelligence.handle("What time is it?") is None
    assert intelligence.handle("yes") is None
    assert memory.catalog.get_memory(record.memory_id) is not None


def test_normal_chat_auto_learns_user_fact_but_not_model_reply(tmp_path: Path) -> None:
    memory, _external = _memory(tmp_path)
    coordinator = Coordinator()
    fake = FakeOllamaClient("I own a secret imaginary spaceship.")
    chat = ChatService(
        coordinator,
        client=fake,
        settings=ChatSettings(),
        memory_context_provider=memory.context_for_prompt,
    )
    assistant = RonAssistant(coordinator, chat, PromptRouter(fake), memory=memory)

    assistant.respond("My tablet is a Nexus 7")

    records = memory.recent_user_memories()
    assert len(records) == 1
    assert "Nexus 7" in records[0].summary
    assert "spaceship" not in records[0].summary


def test_polite_remember_phrase_is_still_deterministic(tmp_path: Path) -> None:
    memory, _external = _memory(tmp_path)
    intelligence = MemoryIntelligence(memory)

    response = intelligence.handle("Could you remember that my amp is a Fender Mustang LT25?")

    assert response is not None and "remember" in response.casefold()
    recalled = memory.recall("amp")
    assert recalled and recalled[0].content == "my amp is a Fender Mustang LT25"


def test_multiple_forget_candidates_require_selection_then_confirmation(tmp_path: Path) -> None:
    memory, _external = _memory(tmp_path)
    first, _ = memory.remember_explicit("My practice amp is a Fender Mustang LT25")
    second, _ = memory.remember_explicit("My backup amp is a small Orange Crush")
    intelligence = MemoryIntelligence(memory)

    choices = intelligence.handle("Forget about amp")
    assert choices is not None and "few possible memories" in choices

    selected = intelligence.handle("forget 2")
    assert selected is not None and "permanent" in selected.casefold()

    intelligence.handle("yes, forget it")
    remaining = {item.memory_id for item in memory.recent_user_memories()}

    assert first.memory_id not in remaining
    assert second.memory_id in remaining


def test_terminal_memory_shortcuts_use_same_memory_path(tmp_path: Path) -> None:
    from io import StringIO

    from ron.terminal import TerminalChat

    memory, _external = _memory(tmp_path)
    coordinator = Coordinator()
    fake = FakeOllamaClient()
    chat = ChatService(coordinator, client=fake, settings=ChatSettings())
    assistant = RonAssistant(coordinator, chat, PromptRouter(fake), memory=memory)
    prompts = iter([
        "/remember my amp is a Fender Mustang LT25",
        "/recall amp",
        "/quit",
    ])
    output = StringIO()
    terminal = TerminalChat(
        assistant,
        input_reader=lambda prompt: next(prompts),
        output=output,
    )

    assert terminal.run() == 0
    rendered = output.getvalue()
    assert "I'll remember that" in rendered
    assert "Fender Mustang LT25" in rendered
    assert fake.requests == []
