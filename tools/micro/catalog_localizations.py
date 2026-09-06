"""Add localization ZIPs to the public catalog by inspecting their manifests.

Run after uploading the exact archives to the chosen GitHub release. Existing
entries remain unchanged; new languages do not require a Configurator rebuild.
"""
import argparse
import hashlib
import json
from pathlib import Path
import re
import zipfile

REPOSITORY = 'https://github.com/berkutx/PortRoyale2mod'
TARGET = '394CE2A48BC6B21708B81A232C7C88459BE7F3C8082CC593AD83B0B8E4C168EC'
TOKEN = re.compile(r'[A-Za-z0-9][A-Za-z0-9._-]{0,63}\Z')


def entry_from_zip(path, tag):
    path = Path(path)
    if not TOKEN.fullmatch(tag) or not path.name.endswith('.pr2loc.zip'):
        raise ValueError('Invalid release tag or localization filename')
    with zipfile.ZipFile(path) as archive:
        infos = [item for item in archive.infolist() if item.filename == 'manifest.json']
        if len(infos) != 1 or infos[0].file_size > 1024 * 1024 or infos[0].compress_type != zipfile.ZIP_STORED:
            raise ValueError('Expected one bounded ZIP_STORED manifest.json')
        manifest = json.loads(archive.read(infos[0]).decode('utf-8'))
    if (manifest.get('schema_version') != 1 or manifest.get('kind') != 'pr2-localization' or
        manifest.get('target') != {'game':'port-royale-2', 'exe_sha256':TARGET}):
        raise ValueError('Unsupported localization target/schema')
    for key in ('locale', 'package_id', 'version'):
        if not isinstance(manifest.get(key), str) or not TOKEN.fullmatch(manifest[key]):
            raise ValueError('Invalid ' + key)
    expected_name = manifest['package_id'] + '-' + manifest['version'] + '.pr2loc.zip'
    if path.name != expected_name:
        raise ValueError('Archive filename must match manifest identity: ' + expected_name)
    if not isinstance(manifest.get('display_name'), str) or not manifest['display_name'].strip():
        raise ValueError('Missing display name')
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(chunk)
    return dict(locale=manifest['locale'], display_name=manifest['display_name'],
                package_id=manifest['package_id'], version=manifest['version'],
                asset_name=path.name, download_url=REPOSITORY+'/releases/download/'+tag+'/'+path.name,
                size=path.stat().st_size, sha256=digest.hexdigest().upper())


def update_catalog(catalog_path, archives, tag):
    path = Path(catalog_path)
    catalog = json.loads(path.read_text(encoding='utf-8-sig'))
    if catalog.get('repository') != REPOSITORY or catalog.get('release', {}).get('tag') != tag:
        raise ValueError('Catalog repository/release mismatch')
    entries = {(entry['package_id'], entry['version']):entry for entry in catalog['packages']}
    for archive in archives:
        entry = entry_from_zip(archive, tag)
        identity = (entry['package_id'], entry['version'])
        if identity in entries and entries[identity] != entry:
            raise ValueError('Refusing to mutate a published package version: ' + str(identity))
        entries[identity] = entry
    catalog['packages'] = sorted(entries.values(), key=lambda p:(p['locale'].casefold(), p['package_id'], p['version']))
    path.write_text(json.dumps(catalog, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
    return catalog


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--catalog', required=True)
    parser.add_argument('--tag', required=True)
    parser.add_argument('archives', nargs='+', help='Exact uploaded *.pr2loc.zip assets')
    args = parser.parse_args()
    catalog = update_catalog(args.catalog, args.archives, args.tag)
    print('Catalog entries:', len(catalog['packages']))
