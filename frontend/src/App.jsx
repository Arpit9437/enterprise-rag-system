import { useCallback, useEffect, useRef, useState } from "react";

const TOKEN_KEY = "enterprise-rag-access-token";
const API_BASE_URL = (import.meta.env.VITE_API_BASE_URL || "").replace(
  /\/+$/,
  "",
);

async function request(path, { token, ...options } = {}) {
  const headers = new Headers(options.headers);
  if (token) headers.set("Authorization", `Bearer ${token}`);
  if (options.body && !(options.body instanceof FormData)) {
    headers.set("Content-Type", "application/json");
  }

  const response = await fetch(`${API_BASE_URL}${path}`, {
    ...options,
    headers,
  });
  if (response.status === 204) return null;

  const contentType = response.headers.get("content-type") || "";
  const data = contentType.includes("application/json")
    ? await response.json()
    : await response.text();

  if (!response.ok) {
    const detail = data?.detail;
    const error = new Error(
      typeof detail === "string"
        ? detail
        : detail?.message || `Request failed (${response.status})`,
    );
    error.status = response.status;
    throw error;
  }
  return data;
}

function App() {
  const [token, setToken] = useState(
    () => window.sessionStorage.getItem(TOKEN_KEY) || "",
  );
  const [workspaceReady, setWorkspaceReady] = useState(
    () => !window.sessionStorage.getItem(TOKEN_KEY),
  );
  const [user, setUser] = useState(null);
  const [workspaceLoadError, setWorkspaceLoadError] = useState("");
  const [tenants, setTenants] = useState([]);
  const [tenantId, setTenantId] = useState("");
  const [conversations, setConversations] = useState([]);
  const [conversationId, setConversationId] = useState("");
  const [messages, setMessages] = useState([]);
  const [loadingConversation, setLoadingConversation] = useState(false);
  const [documents, setDocuments] = useState([]);
  const [mode, setMode] = useState("login");
  const [authForm, setAuthForm] = useState({ email: "", password: "" });
  const [tenantName, setTenantName] = useState("");
  const [composer, setComposer] = useState("");
  const [searchQuery, setSearchQuery] = useState("");
  const [searchResults, setSearchResults] = useState(null);
  const [page, setPage] = useState("chat");
  const [sourcesOpen, setSourcesOpen] = useState(false);
  const [working, setWorking] = useState("");
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [highlightedDocument, setHighlightedDocument] = useState("");
  const [mobileSidebarOpen, setMobileSidebarOpen] = useState(false);
  const messageEnd = useRef(null);
  const documentRefs = useRef(new Map());

  const selectedTenant = tenants.find((tenant) => tenant.id === tenantId);
  const hasProcessingDocuments = documents.some((document) =>
    ["pending", "processing"].includes(document.status),
  );

  const signOut = useCallback(() => {
    window.sessionStorage.removeItem(TOKEN_KEY);
    setToken("");
    setWorkspaceReady(true);
    setUser(null);
    setTenants([]);
    setTenantId("");
    setConversations([]);
    setConversationId("");
    setMessages([]);
    setDocuments([]);
  }, []);

  const loadWorkspace = useCallback(async () => {
    const [currentUser, tenantList] = await Promise.all([
      request("/auth/me", { token }),
      request("/tenants", { token }),
    ]);
    setUser(currentUser);
    setTenants(tenantList);
    setTenantId((current) =>
      tenantList.some((tenant) => tenant.id === current)
        ? current
        : (tenantList[0]?.id ?? ""),
    );
  }, [token]);

  const loadConversations = useCallback(async () => {
    if (!token || !tenantId) {
      setConversations([]);
      return;
    }
    const result = await request(
      `/tenants/${tenantId}/conversations?limit=100&offset=0`,
      { token },
    );
    setConversations(result.conversations);
  }, [tenantId, token]);

  const loadDocuments = useCallback(async () => {
    if (!token || !tenantId) {
      setDocuments([]);
      return;
    }
    const result = await request(
      `/tenants/${tenantId}/documents?limit=100&offset=0`,
      { token },
    );
    setDocuments(result.documents);
  }, [tenantId, token]);

  const loadConversation = useCallback(
    async (id) => {
      if (!id || !tenantId) return { messages: [] };
      return request(
        `/tenants/${tenantId}/conversations/${id}?message_limit=100&message_offset=0`,
        { token },
      );
    },
    [tenantId, token],
  );

  useEffect(() => {
    if (!token) return undefined;
    let active = true;
    loadWorkspace()
      .then(() => setWorkspaceLoadError(""))
      .catch((loadError) => {
        if (!active) return;
        if (loadError.status === 401) signOut();
        else setWorkspaceLoadError(loadError.message);
      })
      .finally(() => {
        if (active) setWorkspaceReady(true);
      });
    return () => {
      active = false;
    };
  }, [loadWorkspace, signOut, token]);

  useEffect(() => {
    setConversationId("");
    setMessages([]);
    setSearchResults(null);
    setPage("chat");
    if (!tenantId || !token) {
      setConversations([]);
      setDocuments([]);
      return undefined;
    }

    let active = true;
    Promise.all([
      request(`/tenants/${tenantId}/conversations?limit=100&offset=0`, {
        token,
      }),
      request(`/tenants/${tenantId}/documents?limit=100&offset=0`, { token }),
    ])
      .then(([conversationResult, documentResult]) => {
        if (active) {
          setConversations(conversationResult.conversations);
          setDocuments(documentResult.documents);
        }
      })
      .catch((loadError) => {
        if (!active) return;
        if (loadError.status === 401) signOut();
        else setError(loadError.message);
      });

    return () => {
      active = false;
    };
  }, [tenantId, token, signOut]);

  useEffect(() => {
    if (!token || !tenantId || !hasProcessingDocuments) return undefined;
    const interval = window.setInterval(() => {
      loadDocuments().catch((loadError) => {
        if (loadError.status === 401) signOut();
        else setError(loadError.message);
      });
    }, 5000);
    return () => {
      window.clearInterval(interval);
    };
  }, [hasProcessingDocuments, loadDocuments, signOut, tenantId, token]);

  useEffect(() => {
    if (!conversationId || !tenantId || !token) {
      setLoadingConversation(false);
      return undefined;
    }
    let active = true;
    setLoadingConversation(true);
    loadConversation(conversationId)
      .then((result) => {
        if (active) setMessages(result.messages);
      })
      .catch((loadError) => {
        if (!active) return;
        if (loadError.status === 401) signOut();
        else setError(loadError.message);
      })
      .finally(() => {
        if (active) setLoadingConversation(false);
      });
    return () => {
      active = false;
    };
  }, [conversationId, loadConversation, signOut, tenantId, token]);

  useEffect(() => {
    messageEnd.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [messages, working]);

  async function submitAuth(event) {
    event.preventDefault();
    setError("");
    setWorking("auth");
    try {
      if (mode === "register") {
        await request("/auth/register", {
          method: "POST",
          body: JSON.stringify(authForm),
        });
        setNotice("Account created. Sign in to continue.");
        setMode("login");
        setAuthForm((current) => ({ ...current, password: "" }));
      } else {
        const result = await request("/auth/login", {
          method: "POST",
          body: JSON.stringify(authForm),
        });
        setWorkspaceReady(false);
        window.sessionStorage.setItem(TOKEN_KEY, result.access_token);
        setToken(result.access_token);
        setNotice("");
      }
    } catch (submitError) {
      setError(submitError.message);
    } finally {
      setWorking("");
    }
  }

  async function createTenant(event) {
    event.preventDefault();
    setError("");
    setWorking("tenant");
    try {
      const created = await request("/tenants", {
        token,
        method: "POST",
        body: JSON.stringify({ name: tenantName }),
      });
      setTenants((current) => [created, ...current]);
      setTenantId(created.id);
      setTenantName("");
    } catch (submitError) {
      setError(submitError.message);
    } finally {
      setWorking("");
    }
  }

  function createConversation() {
    setConversationId("");
    setMessages([]);
    setLoadingConversation(false);
    setPage("chat");
    setSearchResults(null);
    setError("");
    setMobileSidebarOpen(false);
  }

  async function sendMessage(event) {
    event.preventDefault();
    const question = composer.trim();
    if (!question || !tenantId || working === "send") return;
    setError("");
    setComposer("");
    setWorking("send");
    const optimisticMessage = {
      id: `pending-${Date.now()}`,
      role: "user",
      content: question,
      citations: [],
    };
    setMessages((current) => [...current, optimisticMessage]);

    let newlyCreatedId = "";
    let newConversation = null;
    let turnSaved = false;
    try {
      let activeConversationId = conversationId;
      if (!activeConversationId) {
        const created = await request(`/tenants/${tenantId}/conversations`, {
          token,
          method: "POST",
          body: JSON.stringify({}),
        });
        activeConversationId = created.id;
        newlyCreatedId = created.id;
        newConversation = created;
      }

      const turn = await request(
        `/tenants/${tenantId}/conversations/${activeConversationId}/messages`,
        {
          token,
          method: "POST",
          body: JSON.stringify({ question }),
        },
      );
      turnSaved = true;
      setConversationId(activeConversationId);
      setMessages((current) => [
        ...current.filter((message) => message.id !== optimisticMessage.id),
        {
          id: `user-${Date.now()}`,
          role: "user",
          content: turn.question,
          citations: [],
        },
        {
          id: `assistant-${Date.now()}`,
          role: "assistant",
          content: turn.answer,
          citations: turn.citations,
        },
      ]);
      if (newConversation) {
        setConversations((current) => [
          { ...newConversation, title: question.slice(0, 200) },
          ...current,
        ]);
      }
      const refreshes = await Promise.allSettled([
        loadConversation(activeConversationId),
        loadConversations(),
      ]);
      for (const refresh of refreshes) {
        if (refresh.status === "rejected") setError(refresh.reason.message);
        else if (refresh.value?.messages) setMessages(refresh.value.messages);
      }
    } catch (sendError) {
      if (!turnSaved) {
        setMessages((current) =>
          current.filter((message) => message.id !== optimisticMessage.id),
        );
        setComposer(question);
      }
      let errorMessage = turnSaved
        ? `Message sent, but chat refresh failed: ${sendError.message}`
        : sendError.message;
      if (newlyCreatedId && !turnSaved) {
        try {
          await request(
            `/tenants/${tenantId}/conversations/${newlyCreatedId}`,
            { token, method: "DELETE" },
          );
        } catch (cleanupError) {
          errorMessage = `${sendError.message} Could not remove the empty chat: ${cleanupError.message}`;
        }
      }
      setError(errorMessage);
    } finally {
      setWorking("");
    }
  }

  async function searchSources(event) {
    event.preventDefault();
    if (!searchQuery.trim() || !tenantId) return;
    setError("");
    setWorking("search");
    setSearchResults(null);
    try {
      const result = await request(`/tenants/${tenantId}/search`, {
        token,
        method: "POST",
        body: JSON.stringify({ query: searchQuery.trim(), top_k: 10 }),
      });
      setSearchResults(result);
    } catch (searchError) {
      setError(searchError.message);
    } finally {
      setWorking("");
    }
  }

  async function uploadDocument(event) {
    const file = event.target.files?.[0];
    if (!file || !tenantId) return;
    setError("");
    setWorking("upload");
    const formData = new FormData();
    formData.append("file", file);
    try {
      await request(`/tenants/${tenantId}/documents/upload`, {
        token,
        method: "POST",
        body: formData,
      });
      await loadDocuments();
    } catch (uploadError) {
      if (uploadError.status === 503 && uploadError.detail?.document_id) {
        try {
          await loadDocuments();
        } catch (refreshError) {
          setError(`${uploadError.message} Refresh failed: ${refreshError.message}`);
          return;
        }
      }
      setError(uploadError.message);
    } finally {
      setWorking("");
      event.target.value = "";
    }
  }

  async function retryDocument(documentId) {
    setError("");
    try {
      await request(`/tenants/${tenantId}/documents/${documentId}/retry`, {
        token,
        method: "POST",
      });
      await loadDocuments();
    } catch (retryError) {
      setError(retryError.message);
    }
  }

  async function deleteDocument(documentId) {
    const document = documents.find((item) => item.id === documentId);
    if (
      document
      && !window.confirm(`Delete "${document.filename}" and its searchable content?`)
    ) return;
    setError("");
    try {
      await request(`/tenants/${tenantId}/documents/${documentId}`, {
        token,
        method: "DELETE",
      });
      setDocuments((current) =>
        current.filter((document) => document.id !== documentId),
      );
    } catch (deleteError) {
      setError(deleteError.message);
    }
  }

  async function deleteConversation(event, id) {
    event.stopPropagation();
    setError("");
    try {
      await request(`/tenants/${tenantId}/conversations/${id}`, {
        token,
        method: "DELETE",
      });
      setConversations((current) => current.filter((item) => item.id !== id));
      if (conversationId === id) {
        setConversationId("");
        setMessages([]);
      }
    } catch (deleteError) {
      setError(deleteError.message);
    }
  }

  function openDocument(documentId) {
    setSourcesOpen(true);
    setHighlightedDocument(documentId);
    window.setTimeout(() => {
      documentRefs.current.get(documentId)?.scrollIntoView({
        behavior: "smooth",
        block: "center",
      });
    }, 50);
    window.setTimeout(() => setHighlightedDocument(""), 2200);
  }

  if (!token) {
    return (
      <main className="auth-page">
        <section className="auth-card">
          <a className="brand" href="/">
            <span className="brand-mark">f</span> Fieldnotes
          </a>
          <h1>{mode === "login" ? "Sign in" : "Create account"}</h1>
          {error && <div className="error-banner" role="alert">{error}</div>}
          {notice && <div className="notice-banner" role="status">{notice}</div>}
          <form onSubmit={submitAuth}>
            <label>
              Email
              <input
                autoComplete="email"
                required
                type="email"
                value={authForm.email}
                onChange={(event) =>
                  setAuthForm({ ...authForm, email: event.target.value })
                }
              />
            </label>
            <label>
              Password
              <input
                autoComplete={mode === "login" ? "current-password" : "new-password"}
                minLength={mode === "login" ? 1 : 8}
                required
                type="password"
                value={authForm.password}
                onChange={(event) =>
                  setAuthForm({ ...authForm, password: event.target.value })
                }
              />
            </label>
            <button className="primary-button auth-submit" disabled={working === "auth"}>
              {working === "auth"
                ? "Please wait…"
                : mode === "login"
                  ? "Sign in"
                  : "Create account"}
            </button>
          </form>
          <button
            className="quiet-button auth-switch"
            onClick={() => {
              setMode((current) => current === "login" ? "register" : "login");
              setError("");
              setNotice("");
            }}
            type="button"
          >
            {mode === "login" ? "Create an account" : "Back to sign in"}
          </button>
        </section>
      </main>
    );
  }

  if (!workspaceReady) {
    return (
      <main className="loading-page" aria-label="Loading workspace">
        <span className="loading-spinner" />
      </main>
    );
  }

  if (workspaceLoadError) {
    return (
      <main className="auth-page">
        <section className="auth-card">
          <h1>Unable to load workspace</h1>
          <div className="error-banner" role="alert">{workspaceLoadError}</div>
          <button
            className="primary-button auth-submit"
            onClick={() => window.location.reload()}
          >
            Retry
          </button>
          <button className="quiet-button auth-switch" onClick={signOut}>
            Sign out
          </button>
        </section>
      </main>
    );
  }

  if (tenants.length === 0 && !tenantId) {
    return (
      <main className="auth-page">
        <section className="auth-card">
          <a className="brand" href="/">
            <span className="brand-mark">f</span> Fieldnotes
          </a>
          <h1>Create a workspace</h1>
          {error && <div className="error-banner" role="alert">{error}</div>}
          <form onSubmit={createTenant}>
            <label>
              Workspace name
              <input
                autoFocus
                maxLength={200}
                required
                value={tenantName}
                onChange={(event) => setTenantName(event.target.value)}
              />
            </label>
            <button className="primary-button auth-submit" disabled={working === "tenant"}>
              {working === "tenant" ? "Creating…" : "Create workspace"}
            </button>
          </form>
          <button className="quiet-button auth-switch" onClick={signOut}>
            Sign out
          </button>
        </section>
      </main>
    );
  }

  return (
    <div className="workspace">
      {mobileSidebarOpen && (
        <button
          aria-label="Close sidebar"
          className="sidebar-backdrop"
          onClick={() => setMobileSidebarOpen(false)}
        />
      )}
      <aside className={`sidebar ${mobileSidebarOpen ? "sidebar-open" : ""}`}>
        <div className="sidebar-top">
          <a className="brand" href="#" onClick={(event) => event.preventDefault()}>
            <span className="brand-mark">f</span> Fieldnotes
          </a>
          <button
            aria-label="Close sidebar"
            className="quiet-icon mobile-close"
            onClick={() => setMobileSidebarOpen(false)}
          >
            ×
          </button>
        </div>
        <label className="tenant-select-label">
          <span className="sr-only">Workspace</span>
          <select
            aria-label="Workspace"
            disabled={working === "send"}
            value={tenantId}
            onChange={(event) => {
              setTenantId(event.target.value);
              setMobileSidebarOpen(false);
            }}
          >
            {tenants.map((tenant) => (
              <option key={tenant.id} value={tenant.id}>{tenant.name}</option>
            ))}
          </select>
        </label>
        <button
          className="new-chat-button"
          disabled={working === "send"}
          onClick={createConversation}
        >
          <span aria-hidden="true">＋</span> New chat
        </button>
        <div className="conversation-heading">Chats</div>
        <div className="conversation-list">
          {conversations.length === 0 ? (
            <p className="sidebar-empty">No chats yet</p>
          ) : conversations.map((conversation) => (
            <div
              className={`conversation-item ${conversation.id === conversationId && page === "chat" ? "conversation-active" : ""}`}
              key={conversation.id}
            >
              <button
                className="conversation-select"
                disabled={working === "send"}
                onClick={() => {
                  setPage("chat");
                  if (conversation.id !== conversationId) {
                    setConversationId(conversation.id);
                    setMessages([]);
                  }
                  setMobileSidebarOpen(false);
                }}
                title={conversation.title}
              >
                {conversation.title}
              </button>
              <button
                aria-label={`Delete ${conversation.title}`}
                className="conversation-delete"
                onClick={(event) => deleteConversation(event, conversation.id)}
                title="Delete chat"
              >
                ×
              </button>
            </div>
          ))}
        </div>
        <div className="sidebar-actions">
          <button
            className={`sidebar-action ${page === "search" ? "sidebar-action-active" : ""}`}
            disabled={working === "send"}
            onClick={() => {
              setPage("search");
              setSearchResults(null);
              setMobileSidebarOpen(false);
            }}
          >
            <span aria-hidden="true">⌕</span> Search
          </button>
          <button
            className="sidebar-action"
            onClick={() => {
              setSourcesOpen(true);
              setMobileSidebarOpen(false);
            }}
          >
            <span aria-hidden="true">▤</span> Documents
            {documents.length > 0 && <small>{documents.length}</small>}
          </button>
        </div>
        <div className="account-row">
          <div className="avatar">{user?.email?.[0]?.toUpperCase() || "U"}</div>
          <span className="account-email" title={user?.email}>{user?.email}</span>
          <button
            aria-label="Sign out"
            className="quiet-icon"
            onClick={signOut}
            title="Sign out"
          >
            ↪
          </button>
        </div>
      </aside>

      <main className="main-panel">
        <header className="topbar">
          <button
            aria-label="Open sidebar"
            className="quiet-icon menu-button"
            onClick={() => setMobileSidebarOpen(true)}
          >
            ☰
          </button>
          <span className="topbar-tenant">{selectedTenant?.name}</span>
          <button className="topbar-documents" onClick={() => setSourcesOpen(true)}>
            Documents <span>{documents.length}</span>
          </button>
        </header>

        {error && (
          <div className="error-banner page-error" role="alert">
            <span>{error}</span>
            <button aria-label="Dismiss error" onClick={() => setError("")}>×</button>
          </div>
        )}

        {page === "search" ? (
          <section className="search-page">
            <form className="search-form" onSubmit={searchSources}>
              <input
                autoFocus
                aria-label="Search documents"
                maxLength={2000}
                placeholder="Search your documents"
                required
                value={searchQuery}
                onChange={(event) => setSearchQuery(event.target.value)}
              />
              <button
                aria-label="Submit search"
                className="primary-button search-submit"
                disabled={working === "search"}
              >
                {working === "search" ? <span className="spinner" /> : "⌕"}
              </button>
            </form>
            {searchResults && (
              <div className="search-results" aria-live="polite">
                {searchResults.results.length === 0 ? (
                  <p className="search-empty">No results</p>
                ) : searchResults.results.map((result) => {
                  const source = documents.find(
                    (document) => document.id === result.document_id,
                  );
                  return (
                    <article className="search-result" key={result.chunk_id}>
                      <p>{result.content}</p>
                      <button
                        className="source-link"
                        onClick={() => openDocument(result.document_id)}
                      >
                        {source?.filename || result.document_id}
                        <span> · Chunk {result.chunk_index + 1}</span>
                      </button>
                    </article>
                  );
                })}
              </div>
            )}
          </section>
        ) : (
          <section className="chat-page">
            <div className={`message-list ${messages.length === 0 ? "message-list-empty" : ""}`}>
                {loadingConversation ? (
                  <span className="loading-spinner" />
                ) : messages.length === 0 ? (
                <h1>What would you like to know?</h1>
              ) : messages.map((message) => (
                <article className={`message message-${message.role}`} key={message.id}>
                  {message.role === "assistant" ? (
                    <>
                      <div className="assistant-mark" aria-hidden="true">f</div>
                      <div className="message-body">
                        <p>{message.content}</p>
                        {message.citations?.length > 0 && (
                          <div className="message-citations">
                            {message.citations.map((citation) => {
                              const source = documents.find(
                                (document) => document.id === citation.document_id,
                              );
                              return (
                                <button
                                  className="citation-chip"
                                  key={`${citation.source_id}-${citation.chunk_id}`}
                                  onClick={() => openDocument(citation.document_id)}
                                >
                                  <span>{citation.source_id}</span>
                                  {source?.filename || `Source ${citation.source_id}`}
                                  <small>p. {citation.chunk_index + 1}</small>
                                </button>
                              );
                            })}
                          </div>
                        )}
                      </div>
                    </>
                  ) : (
                    <div className="user-bubble">{message.content}</div>
                  )}
                </article>
              ))}
              {working === "send" && (
                <div className="message message-assistant">
                  <div className="assistant-mark" aria-hidden="true">f</div>
                  <div className="thinking"><span /><span /><span /></div>
                </div>
              )}
              <div ref={messageEnd} />
            </div>
            <form className="composer" onSubmit={sendMessage}>
              <textarea
                aria-label="Message"
                maxLength={2000}
                onChange={(event) => setComposer(event.target.value)}
                onKeyDown={(event) => {
                  if (event.key === "Enter" && !event.shiftKey) {
                    event.preventDefault();
                    event.currentTarget.form.requestSubmit();
                  }
                }}
                placeholder="Message Fieldnotes"
                rows={1}
                value={composer}
              />
              <button
                aria-label="Send message"
                className="send-button"
                disabled={!composer.trim() || working === "send"}
              >
                ↑
              </button>
            </form>
          </section>
        )}
      </main>

      {sourcesOpen && (
        <div
          className="modal-backdrop"
          onClick={(event) => {
            if (event.target === event.currentTarget) setSourcesOpen(false);
          }}
        >
          <section aria-labelledby="documents-title" aria-modal="true" className="sources-modal" role="dialog">
            <header className="modal-header">
              <h2 id="documents-title">Documents</h2>
              <button
                aria-label="Close documents"
                className="quiet-icon"
                onClick={() => setSourcesOpen(false)}
              >
                ×
              </button>
            </header>
            <label className="upload-button">
              {working === "upload" ? "Uploading…" : "Upload a document"}
              <input
                accept=".pdf,.txt,application/pdf,text/plain"
                disabled={working === "upload"}
                onChange={uploadDocument}
                type="file"
              />
            </label>
            <div className="source-list">
              {documents.length === 0 ? (
                <p className="sources-empty">No documents</p>
              ) : documents.map((document) => (
                <article
                  className={`source-row ${highlightedDocument === document.id ? "source-highlighted" : ""}`}
                  key={document.id}
                  ref={(element) => {
                    if (element) documentRefs.current.set(document.id, element);
                    else documentRefs.current.delete(document.id);
                  }}
                >
                  <span className={`file-type ${document.content_type === "application/pdf" ? "file-pdf" : "file-txt"}`}>
                    {document.content_type === "application/pdf" ? "PDF" : "TXT"}
                  </span>
                  <span className="source-name">
                    <strong>{document.filename}</strong>
                    <small>{document.status}</small>
                  </span>
                  {document.status === "failed" && (
                    <button className="source-tool" onClick={() => retryDocument(document.id)}>
                      Retry
                    </button>
                  )}
                  <button
                    aria-label={`Delete ${document.filename}`}
                    className="source-tool source-delete"
                    onClick={() => deleteDocument(document.id)}
                    title="Delete document"
                  >
                    ×
                  </button>
                </article>
              ))}
            </div>
          </section>
        </div>
      )}
    </div>
  );
}

export default App;
