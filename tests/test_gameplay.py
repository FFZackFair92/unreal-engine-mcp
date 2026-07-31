"""Test della Fase 6 (gameplay: fisica/navmesh/blackboard/behavior tree/EQS)
della roadmap di parità con ue-mcp.

Blackboard e Behavior Tree rompono il pattern "i sistemi a grafo sono
protetti" delle fasi 2/3/5: qui l'albero (RootNode/Children/Decorators/
Services) è scrivibile davvero, verificato dal vivo su UE 5.8. EQS invece
resta bloccato come UMG/Blueprint/Niagara: solo l'asset è creabile.
"""

import fake_unreal
import pytest

# --------------------------------------------------------------- fisica/collisione


async def test_set_component_physics_applica_e_rilegge(tools):
    await tools.ue_spawn_actor("StaticMeshActor", label="Cassa")

    esito = await tools.ue_set_component_physics(
        "Cassa",
        "StaticMeshComponent0",
        simulate_physics=True,
        collision_enabled="QueryAndPhysics",
        collision_profile="PhysicsActor",
    )

    assert esito["applied"] == {
        "simulate_physics": True,
        "collision_enabled": "QueryAndPhysics",
        "collision_profile": "PhysicsActor",
    }
    assert esito["info"]["simulate_physics"] is True
    assert esito["info"]["collision_profile"] == "PhysicsActor"


async def test_set_component_physics_normalizza_collision_enabled(tools):
    await tools.ue_spawn_actor("StaticMeshActor", label="Cassa2")

    esito = await tools.ue_set_component_physics(
        "Cassa2", "StaticMeshComponent0", collision_enabled="query_and_physics"
    )

    assert esito["info"]["collision_enabled"] == "QueryAndPhysics"


async def test_component_physics_info_su_attore_sconosciuto_fallisce(tools):
    with pytest.raises(RuntimeError):
        await tools.ue_component_physics_info("NonEsiste", "StaticMeshComponent0")


# --------------------------------------------------------------- navmesh


async def test_nav_rebuild_esegue_il_comando_console(tools, unreal):
    esito = await tools.ue_nav_rebuild()

    assert esito["triggered"] is True
    assert esito["is_building"] is False
    assert "RebuildNavigation" in unreal.state["console"]


async def test_nav_query_point_restituisce_il_punto_finto(tools, unreal):
    unreal.state["nav_random_point"] = fake_unreal.Vector(10, 20, 30)

    esito = await tools.ue_nav_query_point({"x": 0, "y": 0, "z": 0}, radius=300.0)

    assert esito["random_reachable_point"] == {"x": 10.0, "y": 20.0, "z": 30.0}


async def test_nav_query_point_nessun_navmesh_restituisce_none(tools):
    esito = await tools.ue_nav_query_point({"x": 0, "y": 0, "z": 0})

    assert esito["random_reachable_point"] is None


async def test_nav_find_path_trovato(tools, unreal):
    unreal.state["nav_path"] = fake_unreal.NavigationPath(
        [fake_unreal.Vector(0, 0, 0), fake_unreal.Vector(100, 0, 0)]
    )

    esito = await tools.ue_nav_find_path({"x": 0, "y": 0, "z": 0}, {"x": 100, "y": 0, "z": 0})

    assert esito["found"] is True
    assert esito["is_valid"] is True
    assert esito["path_points"] == [{"x": 0.0, "y": 0.0, "z": 0.0}, {"x": 100.0, "y": 0.0, "z": 0.0}]


async def test_nav_find_path_non_trovato(tools):
    esito = await tools.ue_nav_find_path({"x": 0, "y": 0, "z": 0}, {"x": 100, "y": 0, "z": 0})

    assert esito["found"] is False


# --------------------------------------------------------------- blackboard


async def test_crea_blackboard(tools):
    esito = await tools.ue_create_blackboard("/Game/AI", "BB_Guard")

    assert esito["created"] is True
    assert esito["path"] == "/Game/AI/BB_Guard"


async def test_blackboard_add_key_e_info(tools):
    await tools.ue_create_blackboard("/Game/AI", "BB_Guard2")

    esito = await tools.ue_blackboard_add_key("/Game/AI/BB_Guard2", "TargetActor", "object")

    assert esito["keys"] == [{"name": "TargetActor", "type": "BlackboardKeyType_Object"}]

    esito2 = await tools.ue_blackboard_add_key("/Game/AI/BB_Guard2", "Alert", "bool")
    assert [k["name"] for k in esito2["keys"]] == ["TargetActor", "Alert"]


async def test_blackboard_add_key_tipo_sconosciuto_fallisce(tools):
    await tools.ue_create_blackboard("/Game/AI", "BB_Guard3")

    with pytest.raises(RuntimeError, match="sconosciuto"):
        await tools.ue_blackboard_add_key("/Game/AI/BB_Guard3", "X", "chissache")


# --------------------------------------------------------------- behavior tree


async def test_crea_behavior_tree_con_root_e_blackboard(tools):
    await tools.ue_create_blackboard("/Game/AI", "BB_BT")

    esito = await tools.ue_create_behavior_tree(
        "/Game/AI", "BT_Guard", blackboard_path="/Game/AI/BB_BT"
    )

    assert esito["created"] is True
    info = await tools.ue_bt_info("/Game/AI/BT_Guard")
    assert info["blackboard"] == "/Game/AI/BB_BT"
    assert info["root"]["class"] == "BTComposite_Selector"
    assert info["root"]["children"] == []


async def test_bt_add_node_task_e_composite_annidato(tools):
    await tools.ue_create_behavior_tree("/Game/AI", "BT_Tree")

    figlio1 = await tools.ue_bt_add_node("/Game/AI/BT_Tree", "root", "BTTask_Wait")
    assert figlio1["node_path"] == "0"

    figlio2 = await tools.ue_bt_add_node("/Game/AI/BT_Tree", "root", "BTComposite_Sequence")
    assert figlio2["node_path"] == "1"

    nipote = await tools.ue_bt_add_node("/Game/AI/BT_Tree", "1", "BTTask_Wait")
    assert nipote["node_path"] == "1.0"

    info = await tools.ue_bt_info("/Game/AI/BT_Tree")
    assert info["root"]["children"][0]["class"] == "BTTask_Wait"
    assert info["root"]["children"][1]["class"] == "BTComposite_Sequence"
    assert info["root"]["children"][1]["children"][0]["class"] == "BTTask_Wait"


async def test_bt_add_node_su_task_fallisce(tools):
    await tools.ue_create_behavior_tree("/Game/AI", "BT_Tree2")
    await tools.ue_bt_add_node("/Game/AI/BT_Tree2", "root", "BTTask_Wait")

    with pytest.raises(RuntimeError, match="composite"):
        await tools.ue_bt_add_node("/Game/AI/BT_Tree2", "0", "BTTask_Wait")


async def test_bt_set_node_property_bindable_da_blackboard(tools):
    await tools.ue_create_behavior_tree("/Game/AI", "BT_Wait")
    await tools.ue_bt_add_node("/Game/AI/BT_Wait", "root", "BTTask_Wait")

    esito = await tools.ue_bt_set_node_property("/Game/AI/BT_Wait", "0", "WaitTime", 3.5)

    assert esito["via"] == "default_value"
    assert esito["applied"] == 3.5


async def test_bt_add_decorator_e_service(tools):
    await tools.ue_create_behavior_tree("/Game/AI", "BT_DecSvc")
    await tools.ue_bt_add_node("/Game/AI/BT_DecSvc", "root", "BTTask_Wait")

    esito_dec = await tools.ue_bt_add_decorator(
        "/Game/AI/BT_DecSvc", "0", "BTDecorator_Blackboard"
    )
    assert esito_dec["decorator_count"] == 1

    esito_svc = await tools.ue_bt_add_service("/Game/AI/BT_DecSvc", "root", "BTService_DefaultFocus")
    assert esito_svc["service_count"] == 1

    info = await tools.ue_bt_info("/Game/AI/BT_DecSvc")
    assert info["root"]["services"] == ["BTService_DefaultFocus"]
    assert info["root"]["children"][0]["decorators"] == ["BTDecorator_Blackboard"]


async def test_bt_add_decorator_su_root_fallisce(tools):
    await tools.ue_create_behavior_tree("/Game/AI", "BT_DecRoot")

    with pytest.raises(RuntimeError, match="root"):
        await tools.ue_bt_add_decorator("/Game/AI/BT_DecRoot", "root", "BTDecorator_Blackboard")


async def test_bt_add_service_su_task_fallisce(tools):
    await tools.ue_create_behavior_tree("/Game/AI", "BT_SvcTask")
    await tools.ue_bt_add_node("/Game/AI/BT_SvcTask", "root", "BTTask_Wait")

    with pytest.raises(RuntimeError, match="composite"):
        await tools.ue_bt_add_service("/Game/AI/BT_SvcTask", "0", "BTService_DefaultFocus")


# --------------------------------------------------------------- EQS


async def test_crea_eqs_asset(tools):
    esito = await tools.ue_create_eqs_asset("/Game/AI", "EQS_FindCover")

    assert esito["created"] is True
    assert esito["path"] == "/Game/AI/EQS_FindCover"


async def test_eqs_asset_gia_esistente_non_lo_ricrea(tools):
    await tools.ue_create_eqs_asset("/Game/AI", "EQS_Dup")

    esito = await tools.ue_create_eqs_asset("/Game/AI", "EQS_Dup")

    assert esito["created"] is False


# --------------------------------------------------------------- GAS


async def test_crea_gameplay_ability_con_policy(tools):
    esito = await tools.ue_create_gameplay_ability(
        "/Game/Abilities", "GA_Dash",
        instancing_policy="instanced_per_actor",
        net_execution_policy="server_only",
    )

    assert esito["created"] is True
    assert esito["applied"] == {
        "instancing_policy": "instanced_per_actor",
        "net_execution_policy": "server_only",
    }


async def test_gameplay_ability_gia_esistente_non_la_ricrea(tools):
    await tools.ue_create_gameplay_ability("/Game/Abilities", "GA_Dup")

    esito = await tools.ue_create_gameplay_ability("/Game/Abilities", "GA_Dup")

    assert esito["created"] is False


async def test_crea_gameplay_effect_con_durata_e_periodo(tools):
    esito = await tools.ue_create_gameplay_effect(
        "/Game/Effects", "GE_Poison", duration_policy="has_duration", period=1.0
    )

    assert esito["created"] is True
    assert esito["applied"] == {"duration_policy": "has_duration", "period": 1.0}


async def test_ge_add_modifier_supera_il_muro_e_persiste(tools):
    await tools.ue_create_blueprint("/Game/Effects", "AS_Combat", parent_class="AttributeSet")
    await tools.ue_create_gameplay_effect("/Game/Effects", "GE_Damage")

    esito = await tools.ue_ge_add_modifier(
        "/Game/Effects/GE_Damage", "/Game/Effects/AS_Combat", "Health", "add", -10.0
    )

    assert esito["modifiers"] == [
        {
            "attribute_name": "Health",
            "attribute_owner": "/Game/Effects/AS_Combat_C",
            "modifier_op": "AddBase",
            "magnitude": -10.0,
        }
    ]

    # rilettura indipendente, come da metodologia delle fasi precedenti
    info = await tools.ue_ge_info("/Game/Effects/GE_Damage")
    assert info["modifiers"] == esito["modifiers"]


async def test_ge_add_modifier_operatori_vari(tools):
    await tools.ue_create_blueprint("/Game/Effects", "AS_Combat2", parent_class="AttributeSet")
    await tools.ue_create_gameplay_effect("/Game/Effects", "GE_Buff")

    esito = await tools.ue_ge_add_modifier(
        "/Game/Effects/GE_Buff", "/Game/Effects/AS_Combat2", "Speed", "multiply_compound", 1.5
    )

    assert esito["modifiers"][0]["modifier_op"] == "MultiplyCompound"
    assert esito["modifiers"][0]["magnitude"] == 1.5


async def test_ge_add_modifier_operatore_sconosciuto_fallisce(tools):
    await tools.ue_create_blueprint("/Game/Effects", "AS_Combat3", parent_class="AttributeSet")
    await tools.ue_create_gameplay_effect("/Game/Effects", "GE_Bad")

    with pytest.raises(RuntimeError, match="sconosciuto"):
        await tools.ue_ge_add_modifier(
            "/Game/Effects/GE_Bad", "/Game/Effects/AS_Combat3", "Health", "chissache", 1.0
        )


async def test_ge_add_component(tools):
    await tools.ue_create_gameplay_effect("/Game/Effects", "GE_Tagged")

    esito = await tools.ue_ge_add_component("/Game/Effects/GE_Tagged", "AssetTagsGameplayEffectComponent")

    assert esito["ge_components"] == ["AssetTagsGameplayEffectComponent"]

    info = await tools.ue_ge_info("/Game/Effects/GE_Tagged")
    assert info["ge_components"] == ["AssetTagsGameplayEffectComponent"]
