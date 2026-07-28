#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
블로그 아키텍처 다이어그램 생성기.

한 번의 정의에서 두 벌을 뽑습니다.

  assets/diagrams/<name>.svg          ← 인라인용. 색을 사이트 CSS 변수(var(--...))로 씀.
                                        {{< diagram >}} shortcode 가 본문에 인라인하므로
                                        라이트/다크 토글에 즉시 반응함. (권장)

  static/images/projects/<name>.svg   ← 폴백용. 색이 리터럴로 박히고 내부에
                                        @media (prefers-color-scheme: dark) 를 가짐.
                                        ![](/images/projects/<name>.svg) 로 쓸 때.

사용:  python3 gen-diagrams.py <블로그_루트>
"""
import os
import sys

# ── 색 토큰: (라이트 리터럴, 다크 리터럴, 사이트 CSS 변수) ────────────────
T = {
    "entry":       ("#FFFFFF", "#18181F", "var(--entry)"),
    "surface":     ("#F8FAFB", "#101014", "var(--fn-diagram-surface)"),
    "border":      ("#E5E7EB", "#2A2A36", "var(--fn-border)"),
    "borderSoft":  ("#CBD5E1", "#3B3B4F", "var(--fn-border-hover)"),
    "accent":      ("#0F6E56", "#5DCAA5", "var(--fn-accent)"),
    "accentBg":    ("#E1F5EE", "#0D2E24", "var(--fn-accent-light)"),
    "text":        ("#111827", "#F1F5F9", "var(--primary)"),
    "sub":         ("#6B7280", "#94A3B8", "var(--secondary)"),
    "muted":       ("#64748B", "#9CA3AF", "var(--fn-diagram-muted)"),
    "arrow":       ("#6B7280", "#94A3B8", "var(--secondary)"),
    "none":        ("none", "none", "none"),
}


def esc(s):
    return str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


class Diagram:
    def __init__(self, name, w, h, title, aria):
        self.name, self.w, self.h = name, w, h
        self.title, self.aria = title, aria
        self.ops = []

    # ── 도형 ────────────────────────────────────────────────
    def rect(self, x, y, w, h, rx=7, fill="entry", stroke="border",
             dash=None, opacity=None, sw=1):
        self.ops.append(("rect", dict(x=x, y=y, w=w, h=h, rx=rx, fill=fill,
                                      stroke=stroke, dash=dash, opacity=opacity, sw=sw)))

    def text(self, x, y, s, size=12.5, weight=None, anchor="middle", fill="text"):
        self.ops.append(("text", dict(x=x, y=y, s=s, size=size, weight=weight,
                                      anchor=anchor, fill=fill)))

    def line(self, x1, y1, x2, y2, dash=None, marker=True):
        self.ops.append(("line", dict(x1=x1, y1=y1, x2=x2, y2=y2, dash=dash, marker=marker)))

    def path(self, d, dash=None, marker=True):
        self.ops.append(("path", dict(d=d, dash=dash, marker=marker)))

    # ── 조합 ────────────────────────────────────────────────
    def node(self, x, y, w, h, title, sub=None, fill="entry", stroke="border",
             dash=None, rx=7):
        self.rect(x, y, w, h, rx=rx, fill=fill, stroke=stroke, dash=dash)
        cx = x + w / 2
        if sub:
            self.text(cx, y + h / 2 - 2, title, 12.5, 600)
            self.text(cx, y + h / 2 + 13, sub, 10, None, fill="sub")
        else:
            self.text(cx, y + h / 2 + 4, title, 12.5, 600)

    def group(self, x, y, w, h, label, soft=False):
        if soft:
            self.rect(x, y, w, h, rx=10, fill="none", stroke="borderSoft", dash="5 3", sw=1.2)
            self.text(x + 12, y + 18, label, 10.5, 700, anchor="start", fill="sub")
        else:
            self.rect(x, y, w, h, rx=10, fill="accentBg", stroke="accent",
                      opacity=0.42, sw=1.2)
            self.text(x + 14, y + 21, label, 11, 700, anchor="start", fill="accent")

    def lane(self, y, label, note=None):
        self.text(24, y, label, 13, 700, anchor="start", fill="accent")
        if note:
            self.text(24 + len(label) * 13 + 10, y, note, 11, None,
                      anchor="start", fill="muted")

    def sep(self, y, x1=24, x2=None):
        self.line(x1, y, x2 or (self.w - 24), y, marker=False)
        self.ops[-1][1]["sep"] = True

    # ── 렌더 ────────────────────────────────────────────────
    def _c(self, token, mode, dark=False):
        lit_l, lit_d, var = T[token]
        if mode == "inline":
            return var
        return lit_d if dark else lit_l

    def _body(self, mode, dark=False):
        mid = f"arw-{self.name}"
        out = []
        for kind, o in self.ops:
            if kind == "rect":
                a = [f'x="{o["x"]}"', f'y="{o["y"]}"', f'width="{o["w"]}"',
                     f'height="{o["h"]}"', f'rx="{o["rx"]}"',
                     f'fill="{self._c(o["fill"], mode, dark)}"',
                     f'stroke="{self._c(o["stroke"], mode, dark)}"',
                     f'stroke-width="{o["sw"]}"']
                if mode == "lit":
                    a += [f'data-f="{o["fill"]}"', f'data-s="{o["stroke"]}"']
                if o["dash"]:
                    a.append(f'stroke-dasharray="{o["dash"]}"')
                if o["opacity"] is not None:
                    a.append(f'fill-opacity="{o["opacity"]}"')
                out.append("<rect " + " ".join(a) + "/>")
            elif kind == "text":
                a = [f'x="{o["x"]}"', f'y="{o["y"]}"', f'font-size="{o["size"]}"',
                     f'text-anchor="{o["anchor"]}"',
                     f'fill="{self._c(o["fill"], mode, dark)}"']
                if mode == "lit":
                    a.append(f'data-f="{o["fill"]}"')
                if o["weight"]:
                    a.append(f'font-weight="{o["weight"]}"')
                out.append("<text " + " ".join(a) + f">{esc(o['s'])}</text>")
            elif kind in ("line", "path"):
                is_sep = o.get("sep")
                col = self._c("border" if is_sep else "arrow", mode, dark)
                a = [f'stroke="{col}"', f'stroke-width="{1 if is_sep else 1.3}"', 'fill="none"']
                if mode == "lit":
                    a.append(f'data-s="{"border" if is_sep else "arrow"}"')
                if o["dash"]:
                    a.append(f'stroke-dasharray="{o["dash"]}"')
                if o.get("marker"):
                    a.append(f'marker-end="url(#{mid})"')
                if kind == "line":
                    a = [f'x1="{o["x1"]}"', f'y1="{o["y1"]}"',
                         f'x2="{o["x2"]}"', f'y2="{o["y2"]}"'] + a
                    out.append("<line " + " ".join(a) + "/>")
                else:
                    out.append(f'<path d="{o["d"]} " ' + " ".join(a) + "/>")
        return "\n".join(out)

    def _defs(self, mode, dark=False):
        mid = f"arw-{self.name}"
        col = self._c("arrow", mode, dark)
        return (f'<defs><marker id="{mid}" viewBox="0 0 10 10" refX="9" refY="5" '
                f'markerWidth="5.5" markerHeight="5.5" orient="auto-start-reverse">'
                f'<path d="M 0 0 L 10 5 L 0 10 z" fill="{col}"/></marker></defs>')

    def render_inline(self):
        """assets/diagrams/ 용 — <style> 없음, 색은 사이트 CSS 변수."""
        return (
            f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {self.w} {self.h}" '
            f'preserveAspectRatio="xMidYMid meet">\n'
            f'<title>{esc(self.title)}</title>\n'
            f'{self._defs("inline")}\n{self._body("inline")}\n</svg>\n')

    def render_standalone(self):
        """static/ 용 — 색 리터럴(라이트) + prefers-color-scheme 로 다크 오버라이드."""
        dark_rules = []
        for k, (_, lit_d, _) in T.items():
            if k == "none":
                continue
            dark_rules.append(f'    [data-f="{k}"] {{ fill: {lit_d}; }}')
            dark_rules.append(f'    [data-s="{k}"] {{ stroke: {lit_d}; }}')
        dark_rules.append(f'    #{f"arw-{self.name}"} path {{ fill: {T["arrow"][1]}; }}')
        rules = "\n".join(dark_rules)
        return (
            f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {self.w} {self.h}" '
            f'width="{self.w}" height="{self.h}" role="img" aria-label="{esc(self.aria)}">\n'
            f'<title>{esc(self.title)}</title>\n'
            f'{self._defs("lit", dark=False)}\n'
            f'<style>\n'
            f'  svg {{ font-family: Pretendard, -apple-system, BlinkMacSystemFont, '
            f'"Segoe UI", Roboto, sans-serif; }}\n'
            f'  @media (prefers-color-scheme: dark) {{\n{rules}\n  }}\n'
            f'</style>\n'
            f'{self._body("lit", dark=False)}\n</svg>\n')


# ════════════════════════════════════════════════════════════════════
# 1. DraWe — 운영 아키텍처
# ════════════════════════════════════════════════════════════════════
def drawe():
    d = Diagram("drawe", 660, 762,
                "DraWe 운영 아키텍처 — 요청 · 배포 · 관측",
                "DraWe 운영 아키텍처 — 요청·배포·관측 세 흐름")

    # ── 요청 ──
    d.lane(30, "요청", "사용자 → 서비스 → 데이터")
    d.node(24, 48, 86, 38, "User")
    d.line(110, 67, 130, 67)
    d.node(130, 41, 168, 52, "Cloudflare", "Pages · DNS · TLS · WAF")
    d.line(298, 67, 318, 67)
    d.node(318, 41, 150, 52, "ALB Ingress", "단일 ALB 공유")
    d.path("M 393 93 L 393 118")

    d.group(24, 124, 612, 128, "EKS · Karpenter · Graviton ARM64")
    d.node(38, 156, 190, 62, "backend", "Spring Boot")
    d.node(236, 156, 190, 62, "fastapi · embed", "CLIP 임베딩")
    d.node(434, 156, 188, 62, "fastapi · guide", "ClusterIP 내부전용")
    d.path("M 330 252 L 330 277")

    # 데이터 · 벡터 · 외부 AI
    d.group(24, 282, 612, 134, "데이터 · 벡터 검색 · 외부 AI", soft=True)
    row1 = [("RDS", "MySQL"), ("ElastiCache", "Valkey"),
            ("S3", "이미지 · 로그"), ("Qdrant", "가이드 레퍼런스")]
    for i, (t, sb) in enumerate(row1):
        d.node(38 + i * 149, 312, 137, 42, t, sb, fill="surface", rx=6)
    row2 = [("Pinecone", "보드 검색"), ("AWS Bedrock", "VLM 관찰 · 이미지 생성"),
            ("Grok", "코칭 문장 생성")]
    for i, (t, sb) in enumerate(row2):
        d.node(38 + i * 149, 364, 137, 42, t, sb, fill="surface", rx=6)
    d.text(553, 381, "벡터 DB 를 용도별로", 9.5, None, fill="muted")
    d.text(553, 395, "가이드 / 보드 분리", 9.5, None, fill="muted")

    # ── 배포 ──
    d.sep(448)
    d.lane(482, "배포", "git = 단일 출처")
    d.node(24, 500, 84, 38, "git push")
    d.line(108, 519, 126, 519)
    d.node(126, 493, 152, 52, "GitHub Actions", "OIDC · ARM64 빌드")
    d.line(278, 519, 296, 519)
    d.node(296, 500, 62, 38, "ECR")
    d.line(358, 519, 376, 519)
    d.node(376, 493, 140, 52, "overlay bump", "kustomize newTag")
    d.line(516, 519, 534, 519)
    d.node(534, 493, 102, 52, "ArgoCD", "auto-sync")
    d.path("M 585 493 L 585 468 L 648 468 L 648 190 L 641 190", dash="4 4")

    # ── 관측 ──
    d.sep(582)
    d.lane(616, "관측", "앱은 OTLP만 안다")
    d.node(24, 634, 100, 38, "앱 (OTLP)")
    d.line(124, 653, 142, 653)
    d.node(142, 627, 160, 52, "Alloy DaemonSet", "PII redaction")
    d.line(302, 653, 320, 653)
    d.node(320, 627, 172, 52, "Loki · Tempo · AMP", "prod 직접 운영")
    d.line(492, 653, 510, 653)
    d.node(510, 634, 126, 38, "Grafana")
    d.node(142, 696, 160, 44, "dev: Grafana Cloud", "비용을 눌러 SaaS로",
           fill="none", dash="4 4")
    d.path("M 222 679 L 222 690", dash="3 3")
    return d


# ════════════════════════════════════════════════════════════════════
# 2. server-job-history — kubeadm lab 아키텍처
# ════════════════════════════════════════════════════════════════════
def sjh():
    d = Diagram("sjh", 660, 560,
                "server-job-history 아키텍처 — 요청 · 배포 · 관측",
                "server-job-history 아키텍처 — 요청·배포·관측 세 흐름")

    # ── 요청 ──
    d.lane(30, "요청", "클라이언트 → 앱 → DB")
    d.node(24, 50, 96, 38, "클라이언트")
    d.line(120, 69, 138, 69)
    d.node(138, 43, 140, 52, "MetalLB", "LoadBalancer / NodePort")
    d.line(278, 69, 296, 69)
    d.group(296, 34, 212, 70, "namespace: app")
    d.node(308, 58, 188, 38, "Deployment", "HPA 2~6")
    d.line(508, 77, 528, 77)
    d.group(528, 34, 108, 70, "namespace: db", soft=True)
    d.node(538, 58, 88, 38, "PostgreSQL", fill="surface")
    d.text(24, 130, "kubeadm 클러스터 — cp-1 + worker-1~3 (flannel · local-path · metrics-server)",
           11, None, anchor="start", fill="muted")

    # ── 배포 ──
    d.sep(152)
    d.lane(186, "배포", "git = 단일 출처 · self-heal")
    d.node(24, 204, 84, 38, "git")
    d.line(108, 223, 126, 223)
    d.node(126, 197, 116, 52, "ArgoCD", "auto-sync")
    d.line(242, 223, 260, 223)
    d.node(260, 197, 168, 52, "PreSync Job", "DB 마이그레이션(멱등)")
    d.line(428, 223, 446, 223)
    d.node(446, 197, 190, 52, "롤링 업데이트", "Deployment 갱신")
    d.node(126, 264, 210, 44, "SealedSecret → Secret", "git엔 암호문만 커밋",
           fill="none", dash="4 4")
    d.node(352, 264, 180, 44, "GHCR", "이미지 pull (regcred)", fill="none", dash="4 4")
    d.path("M 231 264 L 231 253", dash="3 3")
    d.path("M 442 264 L 442 253", dash="3 3")

    # ── 관측 ──
    d.sep(336)
    d.lane(370, "관측", "세 신호를 trace_id 하나로 상관")
    d.node(24, 396, 100, 124, "앱", "metrics · logs · traces")
    for lbl, dst, y in [("/metrics", "Prometheus", 396),
                        ("stdout JSON", "Alloy → Loki", 442),
                        ("OTLP gRPC", "Tempo", 488)]:
        d.line(124, y + 16, 204, y + 16)
        d.text(164, y + 11, lbl, 9.5, None, fill="muted")
        d.node(204, y, 148, 32, dst, fill="surface", rx=6)
        d.line(352, y + 16, 382, y + 16)
    d.node(382, 396, 112, 124, "Grafana", "대시보드 · 상관")
    d.line(494, 458, 516, 458)
    d.node(516, 431, 120, 54, "Alertmanager", "→ Slack #alerts")
    d.text(24, 536, "multi-window SLO (5m + 1h) — 지속 장애만 page",
           11, None, anchor="start", fill="muted")
    return d


def main():
    root = sys.argv[1] if len(sys.argv) > 1 else "."
    inline_dir = os.path.join(root, "assets", "diagrams")
    static_dir = os.path.join(root, "static", "images", "projects")
    os.makedirs(inline_dir, exist_ok=True)
    os.makedirs(static_dir, exist_ok=True)

    for d, fname in [(drawe(), "drawe-architecture.svg"),
                     (sjh(), "sjh-architecture.svg")]:
        with open(os.path.join(inline_dir, fname), "w", encoding="utf-8") as f:
            f.write(d.render_inline())
        with open(os.path.join(static_dir, fname), "w", encoding="utf-8") as f:
            f.write(d.render_standalone())
        print(f"  ✓ assets/diagrams/{fname}")
        print(f"  ✓ static/images/projects/{fname}")


if __name__ == "__main__":
    main()
