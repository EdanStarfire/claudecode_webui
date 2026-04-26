import { copyFile, mkdir, appendFile } from 'node:fs/promises';
import path from 'node:path';
import { randomUUID } from 'node:crypto';
import { fileURLToPath } from 'node:url';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const REPO_ROOT = path.resolve(__dirname, '..', '..', '..');
const TEST_DATA_DIR = path.join(REPO_ROOT, 'data-e2e');
const SAMPLE_PNG = path.join(__dirname, '..', 'fixtures', 'sample-resource.png');

/**
 * Place a sample PNG resource into a session's storage so the gallery shows it.
 * Resources metadata is stored at session_dir/resources/resources.jsonl
 * (matches DataStorageManager.resources_metadata_file in src/data_storage.py).
 */
export async function seedResourceForSession(sessionId: string): Promise<string> {
  const sessionDir = path.join(TEST_DATA_DIR, 'sessions', sessionId);
  const resourcesDir = path.join(sessionDir, 'resources');
  await mkdir(resourcesDir, { recursive: true });

  const resourceId = randomUUID();
  const dest = path.join(resourcesDir, resourceId);
  await copyFile(SAMPLE_PNG, dest);

  const meta = {
    resource_id: resourceId,
    session_id: sessionId,
    title: 'sample.png',
    description: '',
    format: 'png',
    mime_type: 'image/png',
    is_image: true,
    size_bytes: 1,
    original_path: SAMPLE_PNG,
    original_name: 'sample.png',
    timestamp: Date.now() / 1000,
  };
  // Write to resources/resources.jsonl (matches DataStorageManager.resources_metadata_file)
  await appendFile(
    path.join(resourcesDir, 'resources.jsonl'),
    JSON.stringify(meta) + '\n',
  );
  return resourceId;
}
