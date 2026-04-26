import { request, APIRequestContext } from '@playwright/test';

const BACKEND_PORT = Number(process.env.E2E_BACKEND_PORT ?? 8011);
const BACKEND_URL = `http://127.0.0.1:${BACKEND_PORT}`;

export interface SeedResult {
  projectId: string;
  sessionId: string;
}

export async function getApi(): Promise<APIRequestContext> {
  return request.newContext({ baseURL: BACKEND_URL });
}

export async function createProject(api: APIRequestContext, name = 'e2e-project'): Promise<string> {
  const res = await api.post('/api/projects', {
    data: { name, working_directory: '/tmp/e2e' },
  });
  if (!res.ok()) throw new Error(`createProject failed: ${res.status()} ${await res.text()}`);
  const body = await res.json();
  return body.project.project_id;
}

/**
 * Create a session whose `name` matches a fixture directory in src/tests/fixtures/.
 * Mock SDK factory resolves `name` → fixture dir (see web_server._mock_factory_for_fixtures).
 */
export async function createSession(
  api: APIRequestContext,
  projectId: string,
  fixtureName: string,
): Promise<string> {
  const res = await api.post('/api/sessions', {
    data: {
      project_id: projectId,
      name: fixtureName,        // CRITICAL: must equal fixture dir name
      working_directory: '/tmp/e2e',
    },
  });
  if (!res.ok()) throw new Error(`createSession failed: ${res.status()} ${await res.text()}`);
  const body = await res.json();
  return body.session_id;
}

export async function startSession(api: APIRequestContext, sessionId: string): Promise<void> {
  const res = await api.post(`/api/sessions/${sessionId}/start`);
  if (!res.ok()) throw new Error(`startSession failed: ${res.status()} ${await res.text()}`);
}

export async function deleteAllProjects(api: APIRequestContext): Promise<void> {
  const res = await api.get('/api/projects');
  if (!res.ok()) return;
  const body = await res.json();
  const projects = body.projects ?? [];
  for (const p of projects) {
    await api.delete(`/api/projects/${p.project_id}`);
  }
}

/** One-shot helper: full project+session+start in fixture mode. */
export async function seedSessionFromFixture(
  api: APIRequestContext,
  fixtureName: string,
): Promise<SeedResult> {
  const projectId = await createProject(api, `e2e-${fixtureName}`);
  const sessionId = await createSession(api, projectId, fixtureName);
  await startSession(api, sessionId);
  return { projectId, sessionId };
}
