import { useEffect, useState } from 'react';
import { useAgentContext } from '../../agent-context';
import { apiGet, apiPost, apiDelete } from '../../api';
import { dialogs } from '../../dialogs';
import { toastsStore } from '../../toasts';
import { apiUrl } from '../../origin';

interface CollectionStats {
  name: string;
  count: number;
}

interface VectorDbStatus {
  db_path: string;
  size_bytes: number;
  num_documents: number;
  collections: CollectionStats[];
  active_provider: string;
  active_model: string;
}

interface DocumentInfo {
  id: string;
  collection: string;
  text: string;
  metadata: Record<string, unknown>;
  created_at: string;
}

interface DocumentsListResponse {
  documents: DocumentInfo[];
  total: number;
  limit: number;
  offset: number;
}

interface SearchResult {
  id: string;
  collection: string;
  text: string;
  metadata: Record<string, unknown>;
  score: number;
}

export function VectorDbWidget() {
  const [activeTab, setActiveTab] = useState<'overview' | 'search' | 'explorer' | 'insert'>(
    'overview',
  );
  const [status, setStatus] = useState<VectorDbStatus | null>(null);
  const [loadingStatus, setLoadingStatus] = useState(false);
  const [statusError, setStatusError] = useState<string | null>(null);

  // Search tab state
  const [searchQuery, setSearchQuery] = useState('');
  const [searchCollection, setSearchCollection] = useState('');
  const [searchLimit, setSearchLimit] = useState(5);
  const [searchResults, setSearchResults] = useState<SearchResult[]>([]);
  const [searchLoading, setSearchLoading] = useState(false);
  const [searchError, setSearchError] = useState<string | null>(null);

  // Explorer tab state
  const [explorerCollection, setExplorerCollection] = useState<string>('');
  const [explorerDocs, setExplorerDocs] = useState<DocumentInfo[]>([]);
  const [explorerTotal, setExplorerTotal] = useState(0);
  const [explorerOffset, setExplorerOffset] = useState(0);
  const [explorerLimit] = useState(10);
  const [explorerLoading, setExplorerLoading] = useState(false);
  const [explorerError, setExplorerError] = useState<string | null>(null);

  // Insert tab state
  const [insertCollection, setInsertCollection] = useState('');
  const [insertText, setInsertText] = useState('');
  const [insertMetadataJson, setInsertMetadataJson] = useState('{\n  "source": "manual"\n}');
  const [insertLoading, setInsertLoading] = useState(false);
  const [insertError, setInsertError] = useState<string | null>(null);
  const [insertSuccess, setInsertSuccess] = useState(false);

  // Expanded metadata states in lists
  const [expandedDocs, setExpandedDocs] = useState<Record<string, boolean>>({});

  // Expose database context to the agent orchestrator
  useAgentContext(() => {
    return {
      dbPath: status?.db_path ?? null,
      sizeBytes: status?.size_bytes ?? 0,
      totalDocuments: status?.num_documents ?? 0,
      collections: status?.collections?.map((c) => c.name) ?? [],
      activeProvider: status?.active_provider ?? null,
      activeModel: status?.active_model ?? null,
    };
  });

  // Pull model state
  const [pullProgress, setPullProgress] = useState<string | null>(null);
  const [pulling, setPulling] = useState(false);
  const [pullError, setPullError] = useState<string | null>(null);

  const getWarningBannerContent = () => {
    if (!status) return null;
    const provider = status.active_provider;

    if (provider === 'ollama') {
      return (
        <div
          style={{
            display: 'flex',
            justifyContent: 'space-between',
            alignItems: 'center',
            flexWrap: 'wrap',
            gap: '0.75rem',
          }}
        >
          <div>
            <strong>⚠️ Recommended embedding model missing:</strong> For higher search quality and
            standard dimensions, we recommend installing the <strong>all-minilm</strong>{' '}
            (all-MiniLM-L6-v2) embedding model.
          </div>
          {!pulling && !pullProgress && (
            <button
              className="vdb-btn-primary"
              onClick={handlePullModel}
              style={{
                height: 'auto',
                padding: '0.3rem 0.75rem',
                background: '#ffda6a',
                color: '#14161a',
              }}
            >
              Pull all-minilm
            </button>
          )}
        </div>
      );
    } else if (provider === 'lmstudio') {
      return (
        <div>
          <strong>⚠️ Recommended embedding model missing:</strong> We recommend launching{' '}
          <strong>LM Studio</strong>, searching for <strong>all-MiniLM-L6-v2</strong> or{' '}
          <strong>nomic-embed-text</strong>, downloading it, and loading it as the active model.
        </div>
      );
    } else if (provider === 'vllm') {
      return (
        <div>
          <strong>⚠️ Recommended embedding model missing:</strong> We recommend starting your{' '}
          <strong>vLLM</strong> instance with a dedicated embedding model (such as{' '}
          <strong>all-MiniLM-L6-v2</strong>) by passing the <code>--model</code> CLI flag.
        </div>
      );
    } else {
      return (
        <div>
          <strong>⚠️ LLM Agent not configured:</strong> Finish agent onboarding in the chat home
          view, or configure Ollama, LM Studio, or vLLM to use a dedicated embedding model. Fallback
          is currently using local offline heuristics.
        </div>
      );
    }
  };

  const handlePullModel = () => {
    setPulling(true);
    setPullError(null);
    setPullProgress('Starting download of all-minilm...');

    const url = apiUrl('/api/vectordb/pull');

    fetch(url, { method: 'POST' })
      .then(async (response) => {
        if (!response.ok) {
          throw new Error(`Failed to start download: ${response.statusText}`);
        }
        const reader = response.body?.getReader();
        if (!reader) {
          throw new Error('ReadableStream not supported by browser.');
        }

        const decoder = new TextDecoder();
        let buffer = '';

        while (true) {
          const { done, value } = await reader.read();
          if (done) break;

          buffer += decoder.decode(value, { stream: true });
          const lines = buffer.split('\n');
          buffer = lines.pop() ?? '';

          for (const line of lines) {
            if (!line.trim()) continue;
            try {
              const obj = JSON.parse(line);
              if (obj.status) {
                let progressMsg = obj.status;
                if (obj.completed && obj.total) {
                  const percent = Math.round((obj.completed / obj.total) * 100);
                  progressMsg += ` (${percent}%)`;
                }
                setPullProgress(progressMsg);
              }
            } catch {
              // Ignore partial JSON parsing errors
            }
          }
        }

        setPullProgress('Installation completed successfully!');
        setPulling(false);
        fetchStatus();
      })
      .catch((err) => {
        setPullError(String(err));
        setPulling(false);
        setPullProgress(null);
      });
  };

  const fetchStatus = () => {
    setLoadingStatus(true);
    setStatusError(null);
    apiGet<VectorDbStatus>('/vectordb/status')
      .then((data) => {
        setStatus(data);
        // Default collection selections if empty
        if (data.collections.length > 0) {
          if (!searchCollection) setSearchCollection(data.collections[0].name);
          if (!explorerCollection) setExplorerCollection(data.collections[0].name);
        }
      })
      .catch((err: unknown) => {
        setStatusError(String(err));
      })
      .finally(() => {
        setLoadingStatus(false);
      });
  };

  // Initial fetch
  useEffect(() => {
    fetchStatus();
  }, []);

  // Fetch documents when filter/pagination changes
  useEffect(() => {
    if (activeTab === 'explorer') {
      setExplorerLoading(true);
      setExplorerError(null);
      const queryParams = [
        `limit=${explorerLimit}`,
        `offset=${explorerOffset}`,
        explorerCollection ? `collection=${encodeURIComponent(explorerCollection)}` : '',
      ]
        .filter(Boolean)
        .join('&');

      apiGet<DocumentsListResponse>(`/vectordb/documents?${queryParams}`)
        .then((data) => {
          setExplorerDocs(data.documents);
          setExplorerTotal(data.total);
        })
        .catch((err: unknown) => {
          setExplorerError(String(err));
        })
        .finally(() => {
          setExplorerLoading(false);
        });
    }
  }, [activeTab, explorerCollection, explorerOffset]);

  const handleSearch = (e: React.FormEvent) => {
    e.preventDefault();
    if (!searchQuery.trim()) return;
    if (!searchCollection) {
      setSearchError('Please select or insert a collection first.');
      return;
    }

    setSearchLoading(true);
    setSearchError(null);
    apiPost<SearchResult[]>('/vectordb/search', {
      text: searchQuery,
      collection: searchCollection,
      limit: searchLimit,
    })
      .then((results) => {
        setSearchResults(results);
      })
      .catch((err: unknown) => {
        setSearchError(String(err));
      })
      .finally(() => {
        setSearchLoading(false);
      });
  };

  const handleInsert = (e: React.FormEvent) => {
    e.preventDefault();
    setInsertError(null);
    setInsertSuccess(false);

    if (!insertCollection.trim()) {
      setInsertError('Collection name is required.');
      return;
    }
    if (!insertText.trim()) {
      setInsertError('Document text is required.');
      return;
    }

    let parsedMetadata = {};
    try {
      if (insertMetadataJson.trim()) {
        parsedMetadata = JSON.parse(insertMetadataJson);
      }
    } catch {
      setInsertError('Invalid JSON in metadata field.');
      return;
    }

    setInsertLoading(true);
    apiPost('/vectordb/documents', {
      collection: insertCollection.trim(),
      text: insertText.trim(),
      metadata: parsedMetadata,
    })
      .then(() => {
        setInsertSuccess(true);
        setInsertText('');
        fetchStatus(); // refresh collections list
      })
      .catch((err: unknown) => {
        setInsertError(String(err));
      })
      .finally(() => {
        setInsertLoading(false);
      });
  };

  const handleDelete = async (docId: string, refreshCallback?: () => void) => {
    const ok = await dialogs.confirm({
      title: 'Delete document',
      message: "This removes the document and its embeddings. This can't be undone.",
      confirmLabel: 'Delete',
      danger: true,
    });
    if (!ok) return;

    apiDelete<{ deleted: boolean }>(`/vectordb/documents/${docId}`)
      .then(() => {
        fetchStatus();
        if (refreshCallback) refreshCallback();
      })
      .catch((err: unknown) => {
        toastsStore.add('error', 'Delete failed', String(err));
      });
  };

  const toggleMetadata = (id: string) => {
    setExpandedDocs((prev) => ({ ...prev, [id]: !prev[id] }));
  };

  const formatBytes = (bytes: number) => {
    if (bytes === 0) return '0 Bytes';
    const k = 1024;
    const sizes = ['Bytes', 'KB', 'MB'];
    const i = Math.floor(Math.log(bytes) / Math.log(k));
    return parseFloat((bytes / Math.pow(k, i)).toFixed(2)) + ' ' + sizes[i];
  };

  const getScoreColorClass = (score: number) => {
    if (score >= 0.8) return 'score-excellent';
    if (score >= 0.6) return 'score-good';
    if (score >= 0.4) return 'score-fair';
    return 'score-poor';
  };

  return (
    <div className="vdb-container">
      <div className="vdb-header">
        <div className="vdb-tabs">
          <button
            className={`vdb-tab-btn ${activeTab === 'overview' ? 'active' : ''}`}
            onClick={() => setActiveTab('overview')}
          >
            📊 Overview
          </button>
          <button
            className={`vdb-tab-btn ${activeTab === 'search' ? 'active' : ''}`}
            onClick={() => setActiveTab('search')}
          >
            🔍 Semantic Search
          </button>
          <button
            className={`vdb-tab-btn ${activeTab === 'explorer' ? 'active' : ''}`}
            onClick={() => setActiveTab('explorer')}
          >
            📂 Document Explorer
          </button>
          <button
            className={`vdb-tab-btn ${activeTab === 'insert' ? 'active' : ''}`}
            onClick={() => setActiveTab('insert')}
          >
            ➕ Add Document
          </button>
        </div>
        <button className="vdb-btn-refresh" onClick={fetchStatus} disabled={loadingStatus}>
          🔄 {loadingStatus ? 'Refreshing...' : 'Refresh'}
        </button>
      </div>

      <div className="vdb-body">
        {statusError && (
          <div className="vdb-alert error">
            <strong>Error connecting to Vector DB backend:</strong> {statusError}
          </div>
        )}

        {status && !status.active_model.includes('dedicated') && (
          <div
            className="vdb-alert warning"
            style={{
              display: 'flex',
              flexDirection: 'column',
              gap: '0.5rem',
              marginBottom: '1rem',
              background: 'rgba(255, 193, 7, 0.08)',
              border: '1px solid rgba(255, 193, 7, 0.3)',
              color: '#ffda6a',
            }}
          >
            {getWarningBannerContent()}
            {pullProgress && (
              <div
                style={{
                  fontSize: '0.8rem',
                  color: '#fff',
                  display: 'flex',
                  alignItems: 'center',
                  gap: '0.5rem',
                  marginTop: '0.25rem',
                }}
              >
                <span
                  className="vdb-spinner"
                  style={{
                    display: 'inline-block',
                    width: '10px',
                    height: '10px',
                    border: '2px solid #fff',
                    borderTopColor: 'transparent',
                    borderRadius: '50%',
                    animation: 'spin 1s linear infinite',
                  }}
                />
                <span>{pullProgress}</span>
              </div>
            )}
            {pullError && (
              <div style={{ fontSize: '0.8rem', color: '#ea868f', marginTop: '0.25rem' }}>
                Failed to install: {pullError}
              </div>
            )}
          </div>
        )}

        {activeTab === 'overview' && (
          <div className="vdb-tab-content anim-fade-in">
            <div className="vdb-grid">
              <div className="vdb-card">
                <h3>Database Size</h3>
                <div className="vdb-card-val">
                  {status ? formatBytes(status.size_bytes) : '...'}
                </div>
                <div className="vdb-card-sub">
                  {status?.db_path ? `Path: ${status.db_path}` : ''}
                </div>
              </div>
              <div className="vdb-card">
                <h3>Total Documents</h3>
                <div className="vdb-card-val">{status?.num_documents ?? '...'}</div>
                <div className="vdb-card-sub">Across all registered collections</div>
              </div>
              <div className="vdb-card">
                <h3>Active Provider</h3>
                <div className="vdb-card-val">
                  <span className="vdb-badge">{status?.active_provider || 'None'}</span>
                </div>
                <div className="vdb-card-sub">Dialect used for embeddings</div>
              </div>
              <div className="vdb-card">
                <h3>Active Model</h3>
                <div className="vdb-card-val">
                  <span className="vdb-badge model">{status?.active_model || 'None'}</span>
                </div>
                <div className="vdb-card-sub">Fallback uses local heuristics</div>
              </div>
            </div>

            <div className="vdb-section">
              <h2>Registered Collections</h2>
              {status?.collections && status.collections.length > 0 ? (
                <table className="vdb-table">
                  <thead>
                    <tr>
                      <th>Collection Name</th>
                      <th>Document Count</th>
                      <th>Actions</th>
                    </tr>
                  </thead>
                  <tbody>
                    {status.collections.map((col) => (
                      <tr key={col.name}>
                        <td>
                          <strong>{col.name}</strong>
                        </td>
                        <td>{col.count} documents</td>
                        <td>
                          <button
                            className="vdb-table-btn"
                            onClick={() => {
                              setExplorerCollection(col.name);
                              setSearchCollection(col.name);
                              setActiveTab('explorer');
                            }}
                          >
                            Explore
                          </button>
                          <button
                            className="vdb-table-btn accent"
                            onClick={() => {
                              setSearchCollection(col.name);
                              setActiveTab('search');
                            }}
                          >
                            Search
                          </button>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              ) : (
                <p className="vdb-placeholder">
                  No collections created yet. Go to "Add Document" to start.
                </p>
              )}
            </div>
          </div>
        )}

        {activeTab === 'search' && (
          <div className="vdb-tab-content anim-fade-in">
            <form onSubmit={handleSearch} className="vdb-form search">
              <div className="vdb-form-row">
                <div className="vdb-field flex-grow">
                  <label htmlFor="search-q">Query Text</label>
                  <input
                    id="search-q"
                    type="text"
                    placeholder="Enter query to find semantically similar documents..."
                    value={searchQuery}
                    onChange={(e) => setSearchQuery(e.target.value)}
                  />
                </div>
                <div className="vdb-field">
                  <label htmlFor="search-col">Collection</label>
                  <select
                    id="search-col"
                    value={searchCollection}
                    onChange={(e) => setSearchCollection(e.target.value)}
                  >
                    <option value="">-- Select --</option>
                    {status?.collections.map((c) => (
                      <option key={c.name} value={c.name}>
                        {c.name}
                      </option>
                    ))}
                  </select>
                </div>
                <div className="vdb-field">
                  <label htmlFor="search-limit">Limit</label>
                  <input
                    id="search-limit"
                    type="number"
                    min="1"
                    max="50"
                    value={searchLimit}
                    onChange={(e) => setSearchLimit(parseInt(e.target.value) || 5)}
                    style={{ width: '4.5rem' }}
                  />
                </div>
                <button type="submit" className="vdb-btn-primary" disabled={searchLoading}>
                  {searchLoading ? 'Searching...' : 'Search'}
                </button>
              </div>
            </form>

            {searchError && <div className="vdb-alert error">{searchError}</div>}

            <div className="vdb-results">
              <h3>Search Results</h3>
              {searchResults.length > 0 ? (
                <div className="vdb-results-list">
                  {searchResults.map((result) => (
                    <div key={result.id} className="vdb-result-card">
                      <div className="vdb-result-hdr">
                        <span className="vdb-result-id">ID: {result.id}</span>
                        <div className="vdb-result-score-container">
                          <span className={`vdb-score-badge ${getScoreColorClass(result.score)}`}>
                            {(result.score * 100).toFixed(1)}% Match
                          </span>
                          <div className="vdb-score-track">
                            <div
                              className={`vdb-score-fill ${getScoreColorClass(result.score)}`}
                              style={{
                                width: `${Math.max(0, Math.min(100, result.score * 100))}%`,
                              }}
                            />
                          </div>
                        </div>
                      </div>
                      <p className="vdb-result-text">{result.text}</p>

                      <div className="vdb-result-actions">
                        <button className="vdb-card-btn" onClick={() => toggleMetadata(result.id)}>
                          {expandedDocs[result.id] ? 'Hide Metadata' : 'View Metadata'}
                        </button>
                        <button
                          className="vdb-card-btn danger"
                          onClick={() =>
                            handleDelete(result.id, () => {
                              setSearchResults((prev) => prev.filter((r) => r.id !== result.id));
                            })
                          }
                        >
                          Delete
                        </button>
                      </div>

                      {expandedDocs[result.id] && (
                        <pre className="vdb-metadata-box">
                          {JSON.stringify(result.metadata, null, 2)}
                        </pre>
                      )}
                    </div>
                  ))}
                </div>
              ) : (
                <p className="vdb-placeholder">
                  {searchLoading
                    ? 'Running vector search...'
                    : 'Enter a query and click search to view matching documents.'}
                </p>
              )}
            </div>
          </div>
        )}

        {activeTab === 'explorer' && (
          <div className="vdb-tab-content anim-fade-in">
            <div className="vdb-explorer-filters">
              <div className="vdb-field">
                <label htmlFor="exp-col">Filter by Collection:</label>
                <select
                  id="exp-col"
                  value={explorerCollection}
                  onChange={(e) => {
                    setExplorerCollection(e.target.value);
                    setExplorerOffset(0);
                  }}
                >
                  <option value="">All Collections</option>
                  {status?.collections.map((c) => (
                    <option key={c.name} value={c.name}>
                      {c.name}
                    </option>
                  ))}
                </select>
              </div>
              <div className="vdb-explorer-count">
                Showing {explorerDocs.length} of {explorerTotal} documents
              </div>
            </div>

            {explorerError && <div className="vdb-alert error">{explorerError}</div>}

            {explorerDocs.length > 0 ? (
              <div className="vdb-docs-list">
                {explorerDocs.map((doc) => (
                  <div key={doc.id} className="vdb-result-card">
                    <div className="vdb-result-hdr">
                      <span className="vdb-result-id">ID: {doc.id}</span>
                      <span className="vdb-result-col-badge">{doc.collection}</span>
                      <span className="vdb-result-date">
                        {new Date(doc.created_at).toLocaleString()}
                      </span>
                    </div>
                    <p className="vdb-result-text">{doc.text}</p>

                    <div className="vdb-result-actions">
                      <button className="vdb-card-btn" onClick={() => toggleMetadata(doc.id)}>
                        {expandedDocs[doc.id] ? 'Hide Metadata' : 'View Metadata'}
                      </button>
                      <button
                        className="vdb-card-btn danger"
                        onClick={() =>
                          handleDelete(doc.id, () => {
                            setExplorerDocs((prev) => prev.filter((d) => d.id !== doc.id));
                            setExplorerTotal((prev) => prev - 1);
                          })
                        }
                      >
                        Delete
                      </button>
                    </div>

                    {expandedDocs[doc.id] && (
                      <pre className="vdb-metadata-box">
                        {JSON.stringify(doc.metadata, null, 2)}
                      </pre>
                    )}
                  </div>
                ))}

                {explorerTotal > explorerLimit && (
                  <div className="vdb-pagination">
                    <button
                      className="vdb-btn-secondary"
                      disabled={explorerOffset === 0 || explorerLoading}
                      onClick={() => setExplorerOffset((o) => Math.max(0, o - explorerLimit))}
                    >
                      ⬅️ Previous
                    </button>
                    <span className="vdb-page-num">
                      Page {Math.floor(explorerOffset / explorerLimit) + 1} of{' '}
                      {Math.ceil(explorerTotal / explorerLimit)}
                    </span>
                    <button
                      className="vdb-btn-secondary"
                      disabled={explorerOffset + explorerLimit >= explorerTotal || explorerLoading}
                      onClick={() => setExplorerOffset((o) => o + explorerLimit)}
                    >
                      Next ➡️
                    </button>
                  </div>
                )}
              </div>
            ) : (
              <p className="vdb-placeholder">
                {explorerLoading
                  ? 'Loading documents...'
                  : 'No documents found in this collection.'}
              </p>
            )}
          </div>
        )}

        {activeTab === 'insert' && (
          <div className="vdb-tab-content anim-fade-in">
            {insertSuccess && (
              <div className="vdb-alert success">
                <strong>Success!</strong> Document successfully embedded and saved to database.
              </div>
            )}
            {insertError && <div className="vdb-alert error">{insertError}</div>}

            <form onSubmit={handleInsert} className="vdb-insert-form">
              <div className="vdb-field">
                <label htmlFor="ins-col">Collection Name</label>
                <input
                  id="ins-col"
                  type="text"
                  placeholder="e.g. agent_rules, user_settings, memories..."
                  value={insertCollection}
                  onChange={(e) => setInsertCollection(e.target.value)}
                />
              </div>

              <div className="vdb-field">
                <label htmlFor="ins-text">Document Text / Content</label>
                <textarea
                  id="ins-text"
                  placeholder="Enter the textual content to embed and store..."
                  value={insertText}
                  onChange={(e) => setInsertText(e.target.value)}
                  rows={6}
                />
              </div>

              <div className="vdb-field">
                <label htmlFor="ins-meta">Metadata (JSON format)</label>
                <textarea
                  id="ins-meta"
                  placeholder="{}"
                  value={insertMetadataJson}
                  onChange={(e) => setInsertMetadataJson(e.target.value)}
                  rows={4}
                  style={{ fontFamily: 'monospace' }}
                />
              </div>

              <button type="submit" className="vdb-btn-primary" disabled={insertLoading}>
                {insertLoading ? 'Embedding & Saving...' : 'Save Vector & Document'}
              </button>
            </form>
          </div>
        )}
      </div>
    </div>
  );
}
