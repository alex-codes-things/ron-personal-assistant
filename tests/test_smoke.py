from ron.__main__ import main


def test_main_prints_online_message(capsys) -> None:
    main()

    captured = capsys.readouterr()

    assert captured.out.strip() == "Ron is online."