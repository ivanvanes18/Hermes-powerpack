"""Self-improvement guidance must respect the current task's write scope."""
from agent.prompt_builder import SKILLS_GUIDANCE, build_memory_guidance, build_skills_system_prompt


def test_loaded_skills_index_scopes_maintenance_to_authorized_work(tmp_path):
    skill = tmp_path / 'skills' / 'example'
    skill.mkdir(parents=True)
    (skill / 'SKILL.md').write_text('---\nname: example\ndescription: Example fixture.\n---\n# Example\n')
    text = build_skills_system_prompt(
        available_tools={'skill_view', 'skill_manage'},
        available_toolsets={'skills'}, skills_dir_override=tmp_path / 'skills')
    for mode in ('read-only', 'audit', 'staging-only'):
        assert mode in text, f'maintenance lacks scope for {mode}'
    assert 'do not modify installed skills or memory' in text
    assert 'explicit approval' in text
    assert 'example' in text


def test_memory_and_pruning_guidance_keep_learning_but_scope_writes():
    assert build_memory_guidance(False, False) == ''
    for memory_on, user_on in ((True, True), (True, False), (False, True)):
        text = build_memory_guidance(memory_on, user_on)
        assert 'read-only' in text and 'proposed change' in text
        assert 'explicit approval' in text
        assert 'routine learning within authorized work stays available' in text
    assert 'read-only' in SKILLS_GUIDANCE
    assert 'skill_manage' in SKILLS_GUIDANCE
    assert '[SKILL_PRUNED]' in SKILLS_GUIDANCE
