"""
Batch activity without the automatic "Batch Modified" line (Aaron, 2026-09-25):
- saving an existing batch writes nothing by itself - the action that changed it
  (transfer, stage, completion, edit) writes its own specific entry;
- Edit batch writes one "Batch edited" entry listing what actually changed
  (services.save_batch_edit), in the same transaction as the save.
"""
import pytest
from django.contrib.auth import get_user_model
from django.test import Client
from django.urls import reverse

from ..factories import BatchFactory, FermenterFactory, RecipeFactory, VesselFactory
from ..models import AgingTank, Batch, Vessel
from .. import services


@pytest.fixture
def client(db):
    client = Client()
    client.force_login(get_user_model().objects.create_user(username="cellarhand", password="pw"))
    return client


def _batch(**kwargs):
    vessel = VesselFactory(name="Carboy 1", status=Vessel.STATUS_ACTIVE)
    batch = BatchFactory(fermenter=FermenterFactory(vessel=vessel), recipe=RecipeFactory(name="Traditional"),
                         **kwargs)
    batch.vessel = vessel
    batch.save()
    return batch


def _edit(client, batch, **overrides):
    data = {
        "name": batch.name,
        "recipe": batch.recipe.pk if batch.recipe else "",
        "size": str(batch.size),
        "startingGravity": f"{batch.startingGravity.magnitude:.3f}",
        "estimatedEndGravity": f"{batch.estimatedEndGravity.magnitude:.3f}",
    }
    data.update(overrides)
    return client.post(reverse("editBatch", kwargs={"pk": batch.pk}), data)


def _new_entries(batch, before):
    return list(batch.activity.exclude(pk__in=before).order_by("pk").values_list("text", flat=True))


# ---------- No automatic "Batch Modified" ----------

@pytest.mark.django_db
def test_saving_an_existing_batch_writes_no_activity():
    batch = _batch()
    before = list(batch.activity.values_list("pk", flat=True))

    batch.name = "Renamed in code"
    batch.save()

    assert _new_entries(batch, before) == []


@pytest.mark.django_db
def test_creating_a_batch_still_logs_batch_created():
    batch = BatchFactory()

    assert list(batch.activity.values_list("text", flat=True)).count("Batch Created") == 1
    assert not batch.activity.filter(text="Batch Modified").exists()


@pytest.mark.django_db
def test_a_transfer_writes_only_its_own_entry():
    batch = _batch()
    tank = VesselFactory(name="Tank A", status=Vessel.STATUS_READY)
    AgingTank.objects.create(vessel=tank)
    before = list(batch.activity.values_list("pk", flat=True))

    services.transfer_batch(batch, tank, reason="leak")

    assert _new_entries(batch, before) == ["Transferred from [Carboy 1] to [Tank A] :: leak"]


# ---------- Edit batch ----------

@pytest.mark.django_db
def test_an_edit_logs_what_changed(client):
    batch = _batch(name="Spring Mead")
    before = list(batch.activity.values_list("pk", flat=True))

    _edit(client, batch, name="Renamed", startingGravity="1.1")

    assert _new_entries(batch, before) == [
        "Batch edited :: Name [Spring Mead] -> [Renamed]; Starting Gravity [1.090] -> [1.100]"
    ]


@pytest.mark.django_db
def test_an_edit_logs_recipe_size_and_end_gravity_changes(client):
    batch = _batch()
    before = list(batch.activity.values_list("pk", flat=True))

    _edit(client, batch, recipe="", size="5 gallons", estimatedEndGravity="0.998")

    [entry] = _new_entries(batch, before)
    assert entry == ("Batch edited :: Recipe [Traditional] -> [No recipe]; "
                     "Batch Size [6.00 gallon] -> [5.00 gallon]; Estimated End Gravity [1.005] -> [0.998]")


@pytest.mark.django_db
def test_the_same_value_entered_differently_is_not_logged_as_a_change(client):
    batch = _batch(name="Spring Mead")
    before = list(batch.activity.values_list("pk", flat=True))

    # "6 gallons" is the stored "6.00 gallon"; "1.09" is the stored 1.090.
    _edit(client, batch, name="Renamed", size="6 gallons", startingGravity="1.09")

    assert _new_entries(batch, before) == ["Batch edited :: Name [Spring Mead] -> [Renamed]"]


@pytest.mark.django_db
def test_an_edit_that_changes_nothing_logs_nothing(client):
    batch = _batch()
    before = list(batch.activity.values_list("pk", flat=True))

    _edit(client, batch, size="6 gallons")   # re-entered, same value

    assert _new_entries(batch, before) == []


@pytest.mark.django_db
def test_if_the_log_entry_fails_the_edit_is_not_saved(client, monkeypatch):
    batch = _batch(name="Spring Mead")
    before = list(batch.activity.values_list("pk", flat=True))

    def broken(*args, **kwargs):
        raise RuntimeError("log write failed")
    monkeypatch.setattr(services, "add_activity_log", broken)

    response = _edit(client, batch, name="Renamed")

    assert response.status_code == 200
    assert "Nothing was changed" in response.content.decode()
    batch.refresh_from_db()
    assert batch.name == "Spring Mead"
    assert _new_entries(batch, before) == []


@pytest.mark.django_db
def test_save_batch_edit_returns_the_changes():
    batch = _batch(name="Spring Mead")

    changes = services.save_batch_edit(
        batch, name="Renamed", recipe=batch.recipe, size=batch.size,
        starting_gravity=batch.startingGravity.magnitude,
        estimated_end_gravity=batch.estimatedEndGravity.magnitude,
    )

    assert changes == ["Name [Spring Mead] -> [Renamed]"]
    assert Batch.objects.get(pk=batch.pk).name == "Renamed"
