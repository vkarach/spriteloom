import io

from server import models
from server.instruct import _report_tqdm


def test_load_progress_visible_during_factory():
    models.reset()
    seen = {}

    def factory():
        seen["during"] = models.load_progress()
        return object()

    models.register("m", factory)
    assert models.load_progress() is None
    models.get("m")
    assert seen["during"] == (0.0, None)
    assert models.load_progress() is None  # cleared even though no reports
    assert models.is_ready("m")
    models.reset()


def test_load_progress_cleared_after_factory_failure():
    models.reset()
    models.register("bad", lambda: (_ for _ in ()).throw(RuntimeError("x")))
    try:
        models.get("bad")
    except RuntimeError:
        pass
    assert models.load_progress() is None
    models.reset()


def test_load_progress_creeps_toward_the_ceiling(monkeypatch):
    models.reset()
    clock = [1000.0]
    monkeypatch.setattr(models.time, "monotonic", lambda: clock[0])
    models.set_load_progress(0.4, "Loading pipeline components", ceiling=0.9)
    assert models.load_progress() == (0.4, "Loading pipeline components")
    clock[0] += models.CREEP_TAU
    crept, _ = models.load_progress()
    assert 0.4 < crept < 0.9
    clock[0] += 100 * models.CREEP_TAU
    stalled, _ = models.load_progress()
    assert crept < stalled < 0.9
    models.set_load_progress(None)


def test_load_progress_never_drops_when_a_real_step_lands(monkeypatch):
    models.reset()
    clock = [1000.0]
    monkeypatch.setattr(models.time, "monotonic", lambda: clock[0])
    models.set_load_progress(0.4, "x", ceiling=0.9)
    clock[0] += 3 * models.CREEP_TAU
    crept, _ = models.load_progress()
    models.set_load_progress(0.5, "x", ceiling=0.9)
    assert models.load_progress()[0] == crept
    models.set_load_progress(None)


def test_report_tqdm_folds_nested_bars_into_one_fraction():
    import tqdm
    import tqdm.auto
    vals = []
    with _report_tqdm(lambda v, s=None, c=None: vals.append((v, s))):
        outer = tqdm.tqdm(total=2, desc="Loading pipeline components",
                          file=io.StringIO())
        inner = tqdm.auto.tqdm(total=4, desc="Loading checkpoint shards",
                               file=io.StringIO())
        inner.update(2)   # half of the first of two components
        inner.close()     # back to the components bar
        outer.update(1)
        outer.close()
    assert (0.25, "Loading checkpoint shards") in vals
    assert vals[-1] == (0.5, "Loading pipeline components")
    # the patch must not leak outside the context
    assert "Mirror" not in tqdm.tqdm.__name__


def test_report_tqdm_never_steps_back():
    import tqdm
    import tqdm.auto
    vals = []
    with _report_tqdm(lambda v, s=None, c=None: vals.append(v)):
        outer = tqdm.tqdm(total=2, desc="Loading pipeline components",
                          file=io.StringIO())
        for _ in range(2):
            inner = tqdm.auto.tqdm(total=4, desc="Loading checkpoint shards",
                                   file=io.StringIO())
            for _ in range(4):
                inner.update(1)
            inner.close()   # the parent has not ticked yet
            outer.update(1)
        outer.close()
    assert vals == sorted(vals)
    assert vals[-1] == 1.0


def test_report_tqdm_restarts_on_a_new_outermost_bar():
    import tqdm
    vals = []
    with _report_tqdm(lambda v, s=None, c=None: vals.append(v)):
        first = tqdm.tqdm(total=2, desc="Fetching files", file=io.StringIO())
        first.update(2)
        first.close()
        second = tqdm.tqdm(total=4, desc="Loading pipeline components",
                           file=io.StringIO())
        second.update(1)
        second.close()
    assert vals[-1] == 0.25


def test_report_tqdm_sizes_units_by_weight():
    import tqdm
    parts = {"scheduler": 1, "text_encoder": 75, "tokenizer": 1,
             "transformer": 73, "vae": 2}
    vals = []
    with _report_tqdm(lambda v, s=None, c=None: vals.append((v, c)),
                      weigh=lambda names: [parts[n] for n in names]):
        bar = tqdm.tqdm(parts.items(), total=5, desc="Loading pipeline "
                        "components", file=io.StringIO())
        for _ in bar:
            pass
        bar.close()
    starts = sorted({round(v, 4) for v, _ in vals})
    # the two big components own the bar, the three small ones barely move it
    assert starts == [0.0, 0.0066, 0.5, 0.5066, 0.9868, 1.0]
    # the transformer's own span, start and end
    assert [(round(v, 4), round(c, 4)) for v, c in vals
            if round(v, 4) == 0.5066] == [(0.5066, 0.9868)]


def test_report_tqdm_falls_back_to_equal_units_when_unweighable():
    import tqdm
    vals = []
    with _report_tqdm(lambda v, s=None, c=None: vals.append(v),
                      weigh=lambda names: [0 for _ in names]):
        bar = tqdm.tqdm(total=4, desc="Loading checkpoint shards",
                        file=io.StringIO())
        bar.update(1)
        bar.close()
    assert vals[-1] == 0.25


def test_report_tqdm_ignores_totalless_bars():
    import tqdm
    vals = []
    with _report_tqdm(lambda v, s=None, c=None: vals.append((v, s))):
        bar = tqdm.tqdm(file=io.StringIO())  # no total (download-style)
        bar.update(3)
        bar.close()
    assert all(v == (0.0, None) for v in vals)
