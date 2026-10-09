export function resolveProjectVersion(appVersion, currentProjectVersion) {
  // Keep a local label (e.g. 2026.10.0+fork) that marks a fork's own backend.
  if (currentProjectVersion.startsWith(`${appVersion}+`)) {
    return currentProjectVersion;
  }
  const postReleasePrefix = `${appVersion}.post`;
  if (!currentProjectVersion.startsWith(postReleasePrefix)) {
    return appVersion;
  }

  const postReleaseNumber = currentProjectVersion.slice(postReleasePrefix.length);
  return /^[1-9]\d*$/.test(postReleaseNumber) ? currentProjectVersion : appVersion;
}
