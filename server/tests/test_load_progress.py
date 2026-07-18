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


def test_report_tqdm_relays_current_bar_with_label():
    import tqdm
    import tqdm.auto
    vals = []
    with _report_tqdm(lambda v, s=None: vals.append((v, s))):
        outer = tqdm.tqdm(total=2, desc="Loading pipeline components",
                          file=io.StringIO())
        inner = tqdm.auto.tqdm(total=4, desc="Loading checkpoint shards",
                               file=io.StringIO())
        inner.update(2)   # innermost bar wins: 0.5 of the shards stage
        inner.close()     # back to the components bar
        outer.update(1)
        outer.close()
    assert (0.5, "Loading checkpoint shards") in vals
    assert vals[-1] == (0.5, "Loading pipeline components")
    # the patch must not leak outside the context
    assert "Mirror" not in tqdm.tqdm.__name__


def test_report_tqdm_ignores_totalless_bars():
    import tqdm
    vals = []
    with _report_tqdm(lambda v, s=None: vals.append((v, s))):
        bar = tqdm.tqdm(file=io.StringIO())  # no total (download-style)
        bar.update(3)
        bar.close()
    assert all(v == (0.0, None) for v in vals)
