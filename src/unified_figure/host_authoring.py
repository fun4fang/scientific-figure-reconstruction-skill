"""Offline handoff to the user's Codex host. No host launch, auth, or inference."""
from pathlib import Path
import hashlib


def prepare_packet(job):
    from .file_boundary import checked_absolute, write_json, new_file
    from .security import checked_image
    from .analysis.visual_authoring import SCHEMA, RICH_SCHEMA, authoring_instructions
    root = checked_absolute(job).resolve(strict=True)
    source = root / 'input.png'
    pixels = checked_image(source)
    write_json(root, root/'schema.json', SCHEMA)
    write_json(root, root/'rich-schema.json', RICH_SCHEMA)
    # These are task guidance, not a tool policy or host-system replacement.
    for name, correction in (('instructions.md', False), ('correction-instructions.md', True)):
        guidance = authoring_instructions(pixels.size, correction=correction)
        guidance = guidance.replace('Use ONLY attached pixels; no tools, filesystem, web, OCR or historical evidence.',
            'Use ONLY the explicitly approved source pixels as scientific evidence.\n'
            'Host tools are only for this documented handoff, proposal file and local rendering.\n'
            'Do not browse, run OCR, read unrelated files or use historical figures as evidence.')
        with new_file(root, root/name) as stream:
            stream.write(guidance.encode('utf-8'))
    write_json(root, root/'report.json', {
        'status':'PASS', 'mode':'prepare', 'model_authoring':'NOT_EXECUTED',
        'authoring_mode':'CODEX_HOST_MANAGED', 'input':'input.png',
        'width':pixels.width, 'height':pixels.height,
        'input_sha256':hashlib.sha256(source.read_bytes()).hexdigest(),
        'schema':'schema.json', 'rich_schema':'rich-schema.json',
        'instructions':'instructions.md', 'proposal_target':'proposal.json',
        'host_tools_and_permissions':'HOST_MANAGED_NOT_ISOLATED_BY_SKILL',
        'network_or_model_request':False})
