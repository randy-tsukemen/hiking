from yama.yamap import ModelRoute


def _route(**kw) -> ModelRoute:
    base = dict(
        id=181, name="test", distance_m=9362, up_m=1425, down_m=1425,
        time_sec=29400, course_constant=32, fitness_level=3,
        difficulty_level=2, stays=2,
    )
    base.update(kw)
    return ModelRoute(**base)


def test_distance_and_time_formatting():
    r = _route()
    assert r.distance_km == 9.4
    assert r.time_hm == "8:10"
    assert r.url == "https://yamap.com/model-courses/181"


def test_constant_labels():
    assert _route(course_constant=15).constant_label == "輕鬆"
    assert _route(course_constant=32).constant_label == "一般"
    assert _route(course_constant=45).constant_label == "健腳"
    assert _route(course_constant=65).constant_label == "吃力"
    assert _route(course_constant=88).constant_label == "極吃力"
    assert _route(course_constant=None).constant_label == ""


def test_schedule_label():
    assert _route(stays=1).schedule_label == "日帰り"
    assert _route(stays=2).schedule_label == "1泊2日"
    assert _route(stays=4).schedule_label == "3泊4日"


def test_fitness_label():
    assert _route(fitness_level=2).fitness_label == "適合日帰り"
    assert _route(fitness_level=5).fitness_label == "建議住1晚以上"
    assert _route(fitness_level=7).fitness_label == "多日縱走體力"
    assert _route(fitness_level=None).fitness_label == ""
