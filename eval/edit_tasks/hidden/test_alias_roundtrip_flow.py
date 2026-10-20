"""Public alias contracts spanning generation, field preparation, and model I/O."""

from pydantic import AliasChoices, AliasGenerator, BaseModel, ConfigDict, Field


def separate_aliases():
    return AliasGenerator(
        alias=lambda name: "common_" + name,
        validation_alias=lambda name: "input_" + name,
        serialization_alias=lambda name: "output_" + name,
    )


def test_generator_preserves_the_three_public_alias_roles():
    assert separate_aliases().generate_aliases("quantity") == (
        "common_quantity", "input_quantity", "output_quantity"
    )


def test_model_round_trip_preserves_separate_input_and_output_names():
    class Shipment(BaseModel):
        model_config = ConfigDict(alias_generator=separate_aliases())
        quantity: int
        label: str

    item = Shipment.model_validate({"input_quantity": "4", "input_label": "box"})
    assert item.model_dump() == {"quantity": 4, "label": "box"}
    assert item.model_dump(by_alias=True) == {"output_quantity": 4, "output_label": "box"}
    assert Shipment.model_fields["quantity"].alias == "common_quantity"


def test_explicit_high_priority_aliases_survive_generated_aliases():
    class Shipment(BaseModel):
        model_config = ConfigDict(alias_generator=separate_aliases())
        quantity: int = Field(
            alias="explicit_common",
            validation_alias="explicit_input",
            serialization_alias="explicit_output",
            alias_priority=2,
        )

    item = Shipment.model_validate({"explicit_input": 4})
    assert item.model_dump(by_alias=True) == {"explicit_output": 4}


def test_common_alias_is_used_when_separate_generators_are_absent():
    class Shipment(BaseModel):
        model_config = ConfigDict(alias_generator=AliasGenerator(alias=str.upper))
        quantity: int

    assert Shipment.model_validate({"QUANTITY": 4}).model_dump(by_alias=True) == {"QUANTITY": 4}


def test_validation_choices_remain_valid_without_becoming_output_aliases():
    class Shipment(BaseModel):
        model_config = ConfigDict(alias_generator=AliasGenerator(
            alias=lambda name: "common_" + name,
            validation_alias=lambda name: AliasChoices("old_" + name, "new_" + name),
            serialization_alias=lambda name: "output_" + name,
        ))
        quantity: int

    for incoming in ("old_quantity", "new_quantity"):
        assert Shipment.model_validate({incoming: 4}).model_dump(by_alias=True) == {"output_quantity": 4}
