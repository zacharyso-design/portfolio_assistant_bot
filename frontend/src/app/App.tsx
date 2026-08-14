import { useCallback, useEffect, useState } from "react";
import { backend } from "../api/backend";
import { PortfolioPage } from "../features/portfolio/PortfolioPage";
import { ProjectPage } from "../features/project/ProjectPage";
import { ReviewQueuePage } from "../features/review/ReviewQueuePage";
import { SettingsPage } from "../features/settings/SettingsPage";
import { SnowImportPage } from "../features/snow/SnowImportPage";
import { useRoute } from "./router";

export default function App() {
  const { route, navigate } = useRoute();
  const [reviewCount, setReviewCount] = useState(0);
  const refreshReviewCount = useCallback(
    () => backend.reviews.list().then(items => setReviewCount(items.length)).catch(() => undefined),
    [],
  );
  useEffect(() => { refreshReviewCount(); }, [refreshReviewCount, route]);

  return (
    <div className="app-shell">
      <aside className="sidebar">
        <button className="brand" onClick={() => navigate("/")}>
          <span className="logo">PA</span>
          <span><strong>Portfolio</strong><small>Assistant</small></span>
        </button>
        <span className="nav-label">Workspace</span>
        <nav aria-label="Primary navigation">
          <button className={route.page === "portfolio" ? "active" : ""} onClick={() => navigate("/")}><span>▦</span> Portfolio</button>
          <button className={route.page === "review" ? "active" : ""} onClick={() => navigate("/review")}><span>◇</span> Review queue <b>{reviewCount}</b></button>
          <button className={route.page === "import" ? "active" : ""} onClick={() => navigate("/import")}><span>⇩</span> SNOW import</button>
          <button className={route.page === "settings" ? "active" : ""} onClick={() => navigate("/settings")}><span>⚙</span> Settings</button>
        </nav>
        <div className="local-badge"><span>Local</span><small>Government workspace</small></div>
      </aside>
      <main>
        {route.page === "portfolio" && <PortfolioPage navigate={navigate} />}
        {route.page === "project" && route.id && <ProjectPage projectId={route.id} navigate={navigate} />}
        {route.page === "review" && <ReviewQueuePage navigate={navigate} onChange={refreshReviewCount} />}
        {route.page === "import" && <SnowImportPage navigate={navigate} />}
        {route.page === "settings" && <SettingsPage />}
      </main>
    </div>
  );
}
