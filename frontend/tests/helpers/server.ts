import { rm, mkdir } from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const REPO_ROOT = path.resolve(__dirname, '..', '..', '..');
const TEST_DATA_DIR = path.join(REPO_ROOT, 'data-e2e');

/** Wipe + recreate the test data dir. Called from globalSetup. */
export async function resetTestDataDir(): Promise<void> {
  await rm(TEST_DATA_DIR, { recursive: true, force: true });
  // Pre-create subdirectories so the backend works even when reuseExistingServer=true
  await mkdir(path.join(TEST_DATA_DIR, 'projects'), { recursive: true });
  await mkdir(path.join(TEST_DATA_DIR, 'sessions'), { recursive: true });
  await mkdir(path.join(TEST_DATA_DIR, 'legions'), { recursive: true });
  await mkdir(path.join(TEST_DATA_DIR, 'logs'), { recursive: true });
}
