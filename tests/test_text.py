from wordcut.edit import Cut, EditPlan, PlanBuilder, find_phrase, is_filler, map_time, normalize, remap_transcript
from wordcut.models import Segment, Transcript, Word, fill_missing_word_times, fmt_time, join_words, parse_time


# ---------------------------------------------------------------- models
def test_time_parsing_and_format():
    assert parse_time("1:02.5") == 62.5
    assert parse_time("01:00:01") == 3601
    assert parse_time("90") == 90
    assert parse_time("1m30s") == 90
    assert parse_time("2.5s") == 2.5
    assert fmt_time(62.5) == "01:02.500"
    assert fmt_time(3723.004) == "01:02:03.004"


def test_roundtrip(tmp_path, transcript):
    p = transcript.save(tmp_path / "t.json")
    back = Transcript.load(p)
    assert back.to_dict() == transcript.to_dict()
    assert len(back.words()) == 8
    assert back.text.startswith("Olá, pessoal!")


def test_fill_missing_word_times():
    words = [{"word": "a", "start": 1.0, "end": 1.2}, {"word": "1999"}, {"word": "b", "start": 2.0, "end": 2.2}]
    fill_missing_word_times(words, 1.0, 2.2)
    assert words[1]["start"] == 1.2 and words[1]["end"] == 2.0


def test_join_words():
    assert join_words([" Hello", " world"]) == "Hello world"
    assert join_words(["Hello", "world"]) == "Hello world"
    assert join_words(["今日", "は"]) == "今日は"


# ---------------------------------------------------------------- search
def test_normalize():
    assert normalize("Então,") == "entao"
    assert normalize("¿Qué TAL?") == "que tal"


def test_find_phrase_ignores_accents_case_punctuation(transcript):
    hits = find_phrase(transcript, "ola pessoal")
    assert len(hits) == 1
    assert hits[0].start == 0.05 and hits[0].end == 0.95
    assert find_phrase(transcript, "TESTAR isso")[0].start == 4.9
    assert find_phrase(transcript, "ola, pess") == []  # whole words only


def test_find_phrase_cjk():
    t = Transcript(language="ja", segments=[Segment(0, 2, "今日はいい天気", [
        Word("今日", 0, 0.5), Word("は", 0.5, 0.7), Word("いい", 0.7, 1.0), Word("天気", 1.0, 1.6)])])
    hits = find_phrase(t, "はいい")
    assert len(hits) == 1 and hits[0].start == 0.5 and hits[0].end == 1.0


def test_fillers_multilingual():
    for w in ["um", "Uh,", "hmm", "éé", "Ééé...", "hum", "euh", "ähm", "eh", "ahn"]:
        assert is_filler(w), w
    # real words must survive
    for w in ["é", "e", "a", "umbrella", "tipo", "like", "então", "oh"]:
        assert not is_filler(w, "pt"), w
    assert is_filler("tipo", "pt", extra=["tipo"])
    assert is_filler("えーと", "ja")
    assert not is_filler("今日", "ja")


# ---------------------------------------------------------------- planning
def test_plan_merges_and_keeps_complement():
    b = PlanBuilder(10.0, min_keep=0.1)
    b.remove_range(1, 2)
    b.remove_range(1.5, 3)
    b.remove_range(3.05, 4)  # closer than min_keep -> merged
    b.remove_range(8, 9)
    plan = b.build()
    assert [(c.start, c.end) for c in plan.cuts] == [(1, 4), (8, 9)]
    assert plan.keep == [(0.0, 1), (4, 8), (9, 10.0)]
    assert plan.new_duration == 6.0


def test_remove_phrase_pads_into_pauses(transcript):
    b = PlanBuilder(6.0, transcript, max_pad=0.12)
    b.remove_phrase("então")
    (cut,) = b.build().cuts
    # pause before "então" is 0.10 s -> half of it (0.05); after it 0.05 -> 0.025
    assert abs(cut.start - 3.0) < 1e-6 and abs(cut.end - 3.475) < 1e-6
    assert cut.text == "então"


def test_remove_between_and_occurrence(transcript):
    b = PlanBuilder(6.0, transcript)
    m = b.remove_between("então", "testar")
    assert m is not None and "hoje vamos" in m.text
    assert b.build().cuts[0].reason == "between"
    assert PlanBuilder(6.0, transcript).remove_between("testar", "então") is None


def test_remove_fillers(transcript):
    b = PlanBuilder(6.0, transcript)
    assert b.remove_fillers() == 1
    assert b.build().cuts[0].text == "éé"
    b2 = PlanBuilder(6.0, transcript)
    assert b2.remove_fillers(extra=["hoje", "testar isso"]) == 3


def test_silences_keep_a_bit():
    b = PlanBuilder(10.0)
    b.remove_silences([(1.0, 3.0), (5.0, 5.1)], keep=0.2)
    plan = b.build()
    assert [(round(c.start, 3), round(c.end, 3)) for c in plan.cuts] == [(1.1, 2.9)]


def test_transcript_gaps(transcript):
    gaps = PlanBuilder(6.0, transcript).transcript_gaps(0.6)
    assert gaps == [(0.95, 2.55)]


def test_remap_transcript(transcript):
    plan = EditPlan(6.0, [Cut(1.0, 2.5, "x"), Cut(2.5, 3.0, "filler")])
    assert map_time(3.5, plan.keep) == 1.5
    assert map_time(1.5, plan.keep) is None
    edited = remap_transcript(transcript, plan)
    words = [w.text.strip() for w in edited.words()]
    assert "éé" not in words and words[:2] == ["Olá,", "pessoal!"]
    entao = edited.words()[2]
    assert abs(entao.start - 1.05) < 1e-6
    assert abs(edited.duration - 4.0) < 1e-6
