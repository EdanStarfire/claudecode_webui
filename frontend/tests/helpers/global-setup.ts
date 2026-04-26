import { resetTestDataDir } from './server';

export default async function globalSetup() {
  await resetTestDataDir();
}
