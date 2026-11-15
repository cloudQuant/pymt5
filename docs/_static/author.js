/* Progressively enhance the author page with public GitHub metadata. */
(() => {
  "use strict";

  const root = document.querySelector("[data-pymt5-author]");
  if (!root) {
    return;
  }

  const author = root.dataset.pymt5Author;
  if (author !== "cloudQuant") {
    return;
  }

  const CACHE_TTL_MS = 10 * 60 * 1000;
  const REQUEST_TIMEOUT_MS = 8000;
  const controller = new AbortController();
  const timeout = window.setTimeout(() => controller.abort(), REQUEST_TIMEOUT_MS);
  const numberFormat = new Intl.NumberFormat(document.documentElement.lang || "en");
  const dateFormat = new Intl.DateTimeFormat(document.documentElement.lang || "en", {
    year: "numeric",
    month: "short",
    day: "numeric",
  });

  const profileStatus = root.querySelector("[data-author-profile-status]");
  const repositoriesStatus = root.querySelector("[data-author-repositories-status]");
  const stats = root.querySelector("[data-author-stats]");
  const repositoryList = root.querySelector("[data-author-repository-list]");

  function setStatus(element, message) {
    if (element) {
      element.textContent = message;
    }
  }

  function optionalText(value, maximumLength = 500) {
    return typeof value === "string" && value.trim()
      ? value.trim().slice(0, maximumLength)
      : "";
  }

  function publicCount(value) {
    return Number.isSafeInteger(value) && value >= 0 ? value : 0;
  }

  function safeGitHubUrl(value) {
    try {
      const url = new URL(value);
      if (
        url.protocol === "https:" &&
        url.hostname === "github.com" &&
        !url.username &&
        !url.password
      ) {
        return url.href;
      }
    } catch (_error) {
      // Invalid remote URLs are omitted rather than copied into the page.
    }
    return "";
  }

  function sanitizeProfile(value) {
    if (!value || String(value.login).toLowerCase() !== author.toLowerCase()) {
      throw new Error("Unexpected GitHub profile response");
    }
    return {
      name: optionalText(value.name, 120),
      bio: optionalText(value.bio, 500),
      public_repos: publicCount(value.public_repos),
      followers: publicCount(value.followers),
      following: publicCount(value.following),
    };
  }

  function sanitizeRepository(value) {
    if (
      !value ||
      value.private ||
      !value.owner ||
      String(value.owner.login).toLowerCase() !== author.toLowerCase()
    ) {
      return null;
    }
    const name = optionalText(value.name, 120);
    const url = safeGitHubUrl(value.html_url);
    if (!name || !url) {
      return null;
    }
    const updatedAt = typeof value.updated_at === "string" ? Date.parse(value.updated_at) : NaN;
    return {
      name,
      url,
      description: optionalText(value.description, 500),
      language: optionalText(value.language, 60),
      stars: publicCount(value.stargazers_count),
      forks: publicCount(value.forks_count),
      fork: value.fork === true,
      updatedAt: Number.isFinite(updatedAt) ? updatedAt : null,
    };
  }

  function sanitizeRepositories(value) {
    if (!Array.isArray(value)) {
      throw new Error("Unexpected GitHub repository response");
    }
    return value.slice(0, 6).map(sanitizeRepository).filter(Boolean);
  }

  function readCache(key, sanitize) {
    try {
      const cached = JSON.parse(window.sessionStorage.getItem(key));
      if (
        cached &&
        Number.isFinite(cached.timestamp) &&
        Date.now() - cached.timestamp >= 0 &&
        Date.now() - cached.timestamp < CACHE_TTL_MS
      ) {
        return sanitize(cached.data);
      }
    } catch (_error) {
      // Storage can be unavailable in private or restricted browsing contexts.
    }
    return null;
  }

  function writeCache(key, data) {
    try {
      window.sessionStorage.setItem(key, JSON.stringify({ timestamp: Date.now(), data }));
    } catch (_error) {
      // Live rendering still works when storage is unavailable or full.
    }
  }

  async function loadPublicData(kind, sanitize) {
    const key = `pymt5-author:v1:${author}:${kind}`;
    const cached = readCache(key, sanitize);
    if (cached !== null) {
      return cached;
    }
    const suffix = kind === "profile" ? "" : "/repos?per_page=6&sort=updated&type=owner";
    const response = await fetch(`https://api.github.com/users/${author}${suffix}`, {
      headers: { Accept: "application/vnd.github+json" },
      credentials: "omit",
      cache: "no-store",
      referrerPolicy: "no-referrer",
      signal: controller.signal,
    });
    if (!response.ok) {
      throw new Error(`GitHub public API returned HTTP ${response.status}`);
    }
    const payload = await response.json();
    const data = sanitize(payload);
    writeCache(key, payload);
    return data;
  }

  function renderProfile(profile) {
    for (const field of ["public_repos", "followers", "following"]) {
      const element = root.querySelector(`[data-author-field="${field}"]`);
      if (element) {
        element.textContent = numberFormat.format(profile[field]);
      }
    }
    if (stats) {
      stats.hidden = false;
    }

    const displayName = root.querySelector("[data-author-display-name]");
    if (displayName && profile.name) {
      displayName.textContent = `${root.dataset.displayNameLabel}: ${profile.name}`;
      displayName.hidden = false;
    }
    const bio = root.querySelector("[data-author-bio]");
    if (bio && profile.bio) {
      bio.textContent = profile.bio;
      bio.hidden = false;
    }
    setStatus(profileStatus, root.dataset.profileSuccess);
  }

  function appendMetadata(listItem, repository) {
    const metadata = document.createElement("p");
    metadata.className = "pymt5-author-repository-metadata";
    const parts = [];
    if (repository.language) {
      parts.push(repository.language);
    }
    parts.push(`${numberFormat.format(repository.stars)} ${root.dataset.starsLabel}`);
    parts.push(`${numberFormat.format(repository.forks)} ${root.dataset.forksLabel}`);
    if (repository.fork) {
      parts.push(root.dataset.forkLabel);
    }
    if (repository.updatedAt !== null) {
      parts.push(`${root.dataset.updatedLabel} ${dateFormat.format(repository.updatedAt)}`);
    }
    metadata.textContent = parts.join(" · ");
    listItem.append(metadata);
  }

  function renderRepositories(repositories) {
    if (!repositoryList) {
      return;
    }
    if (repositories.length === 0) {
      repositoryList.hidden = true;
      setStatus(repositoriesStatus, root.dataset.repositoriesEmpty);
      return;
    }

    const items = repositories.map((repository) => {
      const item = document.createElement("li");
      item.className = "pymt5-author-repository";
      const heading = document.createElement("h3");
      const link = document.createElement("a");
      link.href = repository.url;
      link.textContent = repository.name;
      heading.append(link);
      item.append(heading);
      const description = document.createElement("p");
      description.textContent = repository.description || root.dataset.noDescription;
      item.append(description);
      appendMetadata(item, repository);
      return item;
    });
    repositoryList.replaceChildren(...items);
    repositoryList.hidden = false;
    setStatus(repositoriesStatus, root.dataset.repositoriesSuccess);
  }

  setStatus(profileStatus, root.dataset.profileLoading);
  setStatus(repositoriesStatus, root.dataset.repositoriesLoading);

  const profileRequest = loadPublicData("profile", sanitizeProfile)
    .then(renderProfile)
    .catch(() => {
      if (stats) {
        stats.hidden = true;
      }
      setStatus(profileStatus, root.dataset.profileError);
    });
  const repositoriesRequest = loadPublicData("repos", sanitizeRepositories)
    .then(renderRepositories)
    .catch(() => {
      if (repositoryList) {
        repositoryList.hidden = true;
      }
      setStatus(repositoriesStatus, root.dataset.repositoriesError);
    });

  Promise.all([profileRequest, repositoriesRequest]).finally(() => window.clearTimeout(timeout));
})();
