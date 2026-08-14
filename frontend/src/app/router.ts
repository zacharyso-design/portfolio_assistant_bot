import { useEffect, useState } from "react";

export type Route = { page: "portfolio" | "project" | "review" | "import" | "settings"; id?: string };
export type Navigate = (path: string) => void;

export function currentRoute(): Route {
  const project = location.pathname.match(/^\/projects\/([^/]+)$/);
  if (project) return { page: "project", id: project[1] };
  if (location.pathname === "/review") return { page: "review" };
  if (location.pathname === "/import") return { page: "import" };
  if (location.pathname === "/settings") return { page: "settings" };
  return { page: "portfolio" };
}

export function useRoute() {
  const [route, setRoute] = useState<Route>(currentRoute);
  useEffect(() => {
    const handler = () => setRoute(currentRoute());
    addEventListener("popstate", handler);
    return () => removeEventListener("popstate", handler);
  }, []);
  const navigate: Navigate = path => {
    history.pushState({}, "", path);
    setRoute(currentRoute());
  };
  return { route, navigate };
}
