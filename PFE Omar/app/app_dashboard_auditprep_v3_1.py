"""
============================================================
PROJET : AuditPrep IA
FICHIER : app_dashboard_auditprep_v3_1.py
VERSION : Dashboard Streamlit V3.1 - Multi-mission sécurisée
OBJET   : Génération dynamique et pilotage de check-lists priorisées
============================================================

Fonctionnalités V3 :
- Connexion PostgreSQL persistante pendant la session ;
- Sélection dynamique :
    * d'une mission cible à préparer ;
    * d'une mission historique source ;
- Lancement direct du moteur SQL :
    auditprep.fn_generate_smart_checklist(...)
- Sélection d'un lot de génération existant ;
- Restitution dynamique :
    * KPI de priorité ;
    * alertes de vigilance ;
    * scores par processus ;
    * scores par clause ISO ;
    * check-list priorisée ;
    * traçabilité du raisonnement ;
- Exports CSV et Excel.

Pré-requis :
- Scripts SQL V1 à V6 exécutés.
"""

from __future__ import annotations

from datetime import datetime
from io import BytesIO
from typing import Any

import pandas as pd
import psycopg2
import streamlit as st


# ============================================================
# 1. CONFIGURATION GÉNÉRALE
# ============================================================

st.set_page_config(
    page_title="AuditPrep IA – Dashboard V3",
    page_icon="🧭",
    layout="wide",
    initial_sidebar_state="expanded",
)

st.markdown(
    """
    <style>
        .block-container {
            padding-top: 1.15rem;
            padding-bottom: 2.2rem;
            max-width: 1500px;
        }

        .hero-box {
            padding: 1.25rem 1.45rem;
            border-radius: 22px;
            background: linear-gradient(135deg, rgba(246,248,251,0.98), rgba(232,237,244,0.90));
            border: 1px solid rgba(78, 88, 108, 0.16);
            margin-bottom: 1.1rem;
        }

        .hero-title {
            font-size: 2.15rem;
            font-weight: 820;
            line-height: 1.12;
            margin-bottom: 0.35rem;
        }

        .hero-subtitle {
            font-size: 1rem;
            opacity: 0.86;
        }

        .panel-box {
            padding: 1.05rem 1.2rem;
            border-radius: 20px;
            background: rgba(248,249,251,0.96);
            border: 1px solid rgba(78, 88, 108, 0.14);
            margin-bottom: 1rem;
        }

        .mission-box {
            padding: 1.05rem 1.25rem;
            border-radius: 20px;
            background: rgba(248,249,251,0.97);
            border: 1px solid rgba(78, 88, 108, 0.14);
            margin-top: 0.6rem;
            margin-bottom: 1rem;
        }

        .mission-title {
            font-size: 1.65rem;
            font-weight: 800;
            line-height: 1.2;
            margin-bottom: 0.35rem;
        }

        .section-caption {
            color: rgba(58,68,86,0.82);
            font-size: 0.93rem;
            margin-top: -0.28rem;
            margin-bottom: 0.85rem;
        }

        .meta-line {
            font-size: 0.98rem;
            margin-bottom: 0.16rem;
        }

        .small-muted {
            opacity: 0.80;
            font-size: 0.92rem;
        }

        .alert-card {
            border-left: 6px solid #5C677D;
            background: rgba(248,249,251,0.97);
            border-radius: 14px;
            padding: 0.78rem 0.95rem;
            margin-bottom: 0.58rem;
            border-top: 1px solid rgba(78, 88, 108, 0.08);
            border-right: 1px solid rgba(78, 88, 108, 0.08);
            border-bottom: 1px solid rgba(78, 88, 108, 0.08);
        }

        .alert-title {
            font-weight: 780;
            font-size: 1rem;
            margin-bottom: 0.18rem;
        }

        .note-box {
            border-radius: 16px;
            border: 1px solid rgba(78, 88, 108, 0.14);
            background: rgba(248,249,251,0.94);
            padding: 0.95rem 1.05rem;
            margin-top: 0.8rem;
            margin-bottom: 0.8rem;
        }

        .pill {
            display: inline-block;
            border-radius: 999px;
            padding: 0.18rem 0.64rem;
            font-size: 0.82rem;
            font-weight: 780;
            margin-left: 0.35rem;
            vertical-align: middle;
        }

        .pill-high {
            background: rgba(184,54,54,0.15);
            color: #8B1E1E;
        }

        .pill-medium {
            background: rgba(186,128,28,0.16);
            color: #8A5A00;
        }

        .pill-low {
            background: rgba(49,123,87,0.16);
            color: #20613E;
        }

        .run-badge {
            display: inline-block;
            padding: 0.18rem 0.58rem;
            border-radius: 999px;
            background: rgba(49, 87, 123, 0.13);
            font-size: 0.82rem;
            font-weight: 760;
        }

        div[data-testid="stMetric"] {
            background: rgba(248,249,251,0.94);
            border: 1px solid rgba(78, 88, 108, 0.12);
            padding: 0.82rem 0.92rem;
            border-radius: 16px;
        }

        .sidebar-note {
            font-size: 0.88rem;
            opacity: 0.82;
        }
    </style>
    """,
    unsafe_allow_html=True,
)


# ============================================================
# 2. REQUÊTES SQL
# ============================================================

SQL_AVAILABLE_HISTORICAL_MISSIONS = """
SELECT
    mission_id,
    mission_code,
    mission_title,
    client_name,
    site_name,
    planned_audit_date,
    findings_count
FROM auditprep.vw_available_historical_missions
ORDER BY planned_audit_date DESC NULLS LAST, mission_code;
"""

SQL_AVAILABLE_TARGET_MISSIONS = """
SELECT
    mission_id,
    mission_code,
    mission_title,
    client_name,
    site_name,
    planned_audit_date,
    standard_label
FROM auditprep.vw_available_target_missions
ORDER BY planned_audit_date DESC NULLS LAST, mission_code;
"""

SQL_GENERATION_RUNS = """
SELECT
    generation_run_id,
    generation_batch_code,
    generated_checklist_title,
    source_mission_code,
    source_mission_title,
    target_mission_code,
    target_mission_title,
    recommendations_count,
    checklist_items_count,
    generated_at
FROM auditprep.vw_dynamic_generation_runs
ORDER BY generated_at DESC, generation_run_id DESC;
"""

SQL_TARGET_MISSION_DETAILS = """
SELECT
    mission_id,
    mission_code,
    mission_title,
    client_name,
    site_name,
    planned_audit_date,
    standard_label
FROM auditprep.vw_available_target_missions
WHERE mission_code = %s
LIMIT 1;
"""

SQL_DYNAMIC_KPIS = """
SELECT
    generation_batch_code,
    target_mission_code,
    source_mission_code,
    generated_priority,
    questions_count
FROM auditprep.vw_dynamic_smart_checklist_kpi_by_priority
WHERE generation_batch_code = %s
ORDER BY generated_priority;
"""

SQL_DYNAMIC_ALERTS = """
SELECT
    source_mission_code,
    alert_dimension,
    alert_key,
    alert_label,
    capped_score,
    vigilance_level,
    explanation_summary
FROM auditprep.vw_dynamic_top_vigilance_alerts
WHERE source_mission_code = %s
ORDER BY capped_score DESC, alert_dimension, alert_key;
"""

SQL_DYNAMIC_PROCESS_VIGILANCE = """
SELECT
    source_mission_code,
    source_mission_title,
    process_name,
    findings_count,
    nonconformities_count,
    remarks_count,
    improvements_count,
    open_corrective_actions_count,
    raw_score,
    capped_score,
    vigilance_level,
    explanation_summary,
    computed_at
FROM auditprep.vw_dynamic_process_vigilance_dashboard
WHERE source_mission_code = %s
ORDER BY capped_score DESC, process_name;
"""

SQL_DYNAMIC_CLAUSE_VIGILANCE = """
SELECT
    source_mission_code,
    source_mission_title,
    clause_code,
    clause_title,
    findings_count,
    nonconformities_count,
    remarks_count,
    improvements_count,
    open_corrective_actions_count,
    raw_score,
    capped_score,
    vigilance_level,
    explanation_summary,
    computed_at
FROM auditprep.vw_dynamic_clause_vigilance_dashboard
WHERE source_mission_code = %s
ORDER BY capped_score DESC, clause_code;
"""

SQL_DYNAMIC_CHECKLIST = """
SELECT
    generation_run_id,
    generation_batch_code,
    target_mission_code,
    target_mission_title,
    source_mission_code,
    source_mission_title,
    checklist_title,
    display_order,
    clause_code,
    clause_title,
    theme,
    question_text,
    generated_priority,
    recommendation_label,
    expected_evidence,
    conformity_status
FROM auditprep.vw_dynamic_smart_checklist_items
WHERE generation_batch_code = %s
ORDER BY display_order;
"""

SQL_DYNAMIC_RECOMMENDATIONS = """
SELECT
    generation_run_id,
    generation_batch_code,
    target_mission_code,
    target_mission_title,
    source_mission_code,
    source_mission_title,
    recommendation_id,
    clause_code,
    clause_title,
    process_name,
    source_dimension,
    question_template,
    clause_vigilance_score,
    process_vigilance_score,
    retained_score,
    generated_priority,
    recommendation_label,
    prioritization_reason,
    generated_at
FROM auditprep.vw_dynamic_smart_checklist_recommendations
WHERE generation_batch_code = %s
ORDER BY retained_score DESC, generated_priority, clause_code NULLS LAST;
"""

SQL_GENERATE_SMART_CHECKLIST = """
SELECT *
FROM auditprep.fn_generate_smart_checklist(%s, %s);
"""


# ============================================================
# 3. FONCTIONS UTILITAIRES
# ============================================================

def connect_db(
    host: str,
    port: int,
    dbname: str,
    user: str,
    password: str,
) -> psycopg2.extensions.connection:
    return psycopg2.connect(
        host=host,
        port=port,
        dbname=dbname,
        user=user,
        password=password,
        connect_timeout=8,
    )


def read_df(
    conn: psycopg2.extensions.connection,
    query: str,
    params: tuple[Any, ...] | None = None,
) -> pd.DataFrame:
    return pd.read_sql_query(query, conn, params=params)


def execute_generate_function(
    conn: psycopg2.extensions.connection,
    target_mission_code: str,
    source_mission_code: str,
) -> pd.DataFrame:
    with conn.cursor() as cur:
        cur.execute(SQL_GENERATE_SMART_CHECKLIST, (target_mission_code, source_mission_code))
        columns = [desc[0] for desc in cur.description]
        rows = cur.fetchall()
    conn.commit()
    return pd.DataFrame(rows, columns=columns)


def load_reference_data(
    host: str,
    port: int,
    dbname: str,
    user: str,
    password: str,
) -> dict[str, pd.DataFrame]:
    conn = connect_db(host, port, dbname, user, password)
    try:
        return {
            "historical_missions": read_df(conn, SQL_AVAILABLE_HISTORICAL_MISSIONS),
            "target_missions": read_df(conn, SQL_AVAILABLE_TARGET_MISSIONS),
            "runs": read_df(conn, SQL_GENERATION_RUNS),
        }
    finally:
        conn.close()


def load_run_dashboard_data(
    host: str,
    port: int,
    dbname: str,
    user: str,
    password: str,
    generation_batch_code: str,
    source_mission_code: str,
    target_mission_code: str,
) -> dict[str, pd.DataFrame]:
    conn = connect_db(host, port, dbname, user, password)
    try:
        return {
            "target_mission": read_df(conn, SQL_TARGET_MISSION_DETAILS, (target_mission_code,)),
            "kpis": read_df(conn, SQL_DYNAMIC_KPIS, (generation_batch_code,)),
            "alerts": read_df(conn, SQL_DYNAMIC_ALERTS, (source_mission_code,)),
            "process": read_df(conn, SQL_DYNAMIC_PROCESS_VIGILANCE, (source_mission_code,)),
            "clause": read_df(conn, SQL_DYNAMIC_CLAUSE_VIGILANCE, (source_mission_code,)),
            "checklist": read_df(conn, SQL_DYNAMIC_CHECKLIST, (generation_batch_code,)),
            "recommendations": read_df(conn, SQL_DYNAMIC_RECOMMENDATIONS, (generation_batch_code,)),
        }
    finally:
        conn.close()


def generate_and_refresh(
    host: str,
    port: int,
    dbname: str,
    user: str,
    password: str,
    target_mission_code: str,
    source_mission_code: str,
) -> tuple[pd.DataFrame, dict[str, pd.DataFrame]]:
    conn = connect_db(host, port, dbname, user, password)
    try:
        generation_result = execute_generate_function(
            conn,
            target_mission_code=target_mission_code,
            source_mission_code=source_mission_code,
        )
    finally:
        conn.close()

    reference_data = load_reference_data(host, port, dbname, user, password)
    return generation_result, reference_data


def fmt_date(value: Any) -> str:
    if pd.isna(value):
        return "Date non renseignée"
    if hasattr(value, "strftime"):
        return value.strftime("%d/%m/%Y")
    try:
        return datetime.strptime(str(value), "%Y-%m-%d").strftime("%d/%m/%Y")
    except ValueError:
        return str(value)


def fmt_datetime(value: Any) -> str:
    if pd.isna(value):
        return "Date non renseignée"
    if hasattr(value, "strftime"):
        return value.strftime("%d/%m/%Y %H:%M")
    return str(value)


def safe_int(value: Any, default: int = 0) -> int:
    try:
        if pd.isna(value):
            return default
        return int(value)
    except (TypeError, ValueError):
        return default


def safe_float(value: Any, default: float = 0.0) -> float:
    try:
        if pd.isna(value):
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def priority_counts(kpi_df: pd.DataFrame) -> dict[str, int]:
    out = {"Haute": 0, "Moyenne": 0, "Faible": 0}
    if kpi_df.empty:
        return out

    for _, row in kpi_df.iterrows():
        label = str(row.get("generated_priority", "")).strip()
        if label in out:
            out[label] = safe_int(row.get("questions_count", 0))
    return out


def pill(label: str) -> str:
    clean = str(label or "").strip()
    if clean in ("Élevée", "Haute"):
        cls = "pill-high"
    elif clean in ("Modérée", "Moyenne"):
        cls = "pill-medium"
    else:
        cls = "pill-low"
    return f'<span class="pill {cls}">{clean}</span>'


def style_priority(value: Any) -> str:
    if str(value) == "Haute":
        return "font-weight: 780;"
    if str(value) == "Moyenne":
        return "font-weight: 720;"
    return ""


def build_excel_export(sheets: dict[str, pd.DataFrame]) -> bytes:
    output = BytesIO()
    with pd.ExcelWriter(output, engine="openpyxl") as writer:
        for sheet_name, df in sheets.items():
            df.to_excel(writer, sheet_name=sheet_name[:31], index=False)
    return output.getvalue()


def mission_label(row: pd.Series) -> str:
    date_txt = fmt_date(row.get("planned_audit_date"))
    return f"{row['mission_code']} — {row['mission_title']} | {row['client_name']} | {date_txt}"


def run_label(row: pd.Series) -> str:
    return (
        f"{row['generation_batch_code']} | "
        f"{row['target_mission_code']} ← {row['source_mission_code']} | "
        f"{fmt_datetime(row['generated_at'])}"
    )


def render_latest_run_box(runs_df: pd.DataFrame) -> None:
    if runs_df.empty:
        return

    latest = runs_df.sort_values(
        by=["generated_at", "generation_run_id"],
        ascending=[False, False],
    ).iloc[0]

    st.markdown(
        f"""
        <div class="panel-box">
            <b>Dernier lot généré :</b> {latest['generation_batch_code']}<br>
            <span class="small-muted">
                Cible : {latest['target_mission_code']} ·
                Historique : {latest['source_mission_code']} ·
                Questions : {safe_int(latest['checklist_items_count'])} ·
                Généré le : {fmt_datetime(latest['generated_at'])}
            </span>
        </div>
        """,
        unsafe_allow_html=True,
    )


# ============================================================
# 4. SESSION STATE
# ============================================================

if "connection_ready" not in st.session_state:
    st.session_state.connection_ready = False

if "reference_data" not in st.session_state:
    st.session_state.reference_data = None

if "dashboard_data" not in st.session_state:
    st.session_state.dashboard_data = None

if "selected_batch_code" not in st.session_state:
    st.session_state.selected_batch_code = None

if "selected_source_code" not in st.session_state:
    st.session_state.selected_source_code = None

if "selected_target_code" not in st.session_state:
    st.session_state.selected_target_code = None

if "last_generation_result" not in st.session_state:
    st.session_state.last_generation_result = None

if "db_settings" not in st.session_state:
    st.session_state.db_settings = {
        "host": "localhost",
        "port": 5432,
        "dbname": "auditprep_ia",
        "user": "postgres",
        "password": "",
    }


# ============================================================
# 5. SIDEBAR – CONNEXION
# ============================================================

st.sidebar.title("Connexion PostgreSQL")
st.sidebar.caption("Connexion locale au moteur AuditPrep IA.")

settings = st.session_state.db_settings

host = st.sidebar.text_input("Hôte", value=settings["host"])
port = st.sidebar.number_input("Port", min_value=1, max_value=65535, value=int(settings["port"]), step=1)
dbname = st.sidebar.text_input("Base de données", value=settings["dbname"])
user = st.sidebar.text_input("Utilisateur", value=settings["user"])
password = st.sidebar.text_input("Mot de passe PostgreSQL", value=settings["password"], type="password")

connect_clicked = st.sidebar.button(
    "Charger les missions",
    type="primary",
    use_container_width=True,
)

if connect_clicked:
    if not password:
        st.sidebar.warning("Le mot de passe PostgreSQL est requis.")
    else:
        try:
            with st.spinner("Connexion et chargement des missions..."):
                ref_data = load_reference_data(host, int(port), dbname, user, password)
            st.session_state.connection_ready = True
            st.session_state.reference_data = ref_data
            st.session_state.db_settings = {
                "host": host,
                "port": int(port),
                "dbname": dbname,
                "user": user,
                "password": password,
            }
            st.sidebar.success("Missions chargées.")
        except Exception as exc:
            st.session_state.connection_ready = False
            st.session_state.reference_data = None
            st.sidebar.error("Connexion ou chargement impossible.")
            st.sidebar.code(str(exc))

st.sidebar.divider()
st.sidebar.markdown("### Version")
st.sidebar.markdown("- Dashboard : **V3.1 multi-mission sécurisée**\n- Moteur SQL : **V6**")
st.sidebar.markdown(
    '<div class="sidebar-note">Le moteur peut générer une check-list priorisée à partir du couple mission cible / mission historique choisi.</div>',
    unsafe_allow_html=True,
)


# ============================================================
# 6. ENTÊTE
# ============================================================

st.markdown(
    """
    <div class="hero-box">
        <div class="hero-title">AuditPrep IA – Pilotage multi-mission sécurisé de la préparation d’audit</div>
        <div class="hero-subtitle">
            Sélectionne une mission cible et un historique d’audit, lance le moteur de priorisation,
            puis consulte automatiquement les KPI, alertes, scores de vigilance et la check-list produite.
        </div>
    </div>
    """,
    unsafe_allow_html=True,
)

if not st.session_state.connection_ready or st.session_state.reference_data is None:
    st.info("Commence par saisir ton mot de passe PostgreSQL à gauche, puis clique sur **Charger les missions**.")
    st.stop()

reference_data = st.session_state.reference_data
historical_df = reference_data["historical_missions"].copy()
target_df = reference_data["target_missions"].copy()
runs_df = reference_data["runs"].copy()

if historical_df.empty:
    st.error("Aucune mission historique exploitable n’est disponible.")
    st.stop()

if target_df.empty:
    st.error("Aucune mission cible n’est disponible.")
    st.stop()


# ============================================================
# 7. PARAMÉTRAGE DE LA GÉNÉRATION
# ============================================================

render_latest_run_box(runs_df)

st.subheader("1. Paramétrer une génération intelligente")
st.markdown(
    '<div class="section-caption">Choisis la mission à préparer et la mission historique dont les constats alimentent le score de vigilance.</div>',
    unsafe_allow_html=True,
)

historical_labels = {mission_label(row): row["mission_code"] for _, row in historical_df.iterrows()}
target_labels = {mission_label(row): row["mission_code"] for _, row in target_df.iterrows()}

default_source_index = 0
default_target_index = 0

if st.session_state.selected_source_code in historical_labels.values():
    default_source_index = list(historical_labels.values()).index(st.session_state.selected_source_code)

if st.session_state.selected_target_code in target_labels.values():
    default_target_index = list(target_labels.values()).index(st.session_state.selected_target_code)

select_col_1, select_col_2 = st.columns(2)

with select_col_1:
    selected_target_label = st.selectbox(
        "Mission cible à préparer",
        options=list(target_labels.keys()),
        index=default_target_index,
    )
    selected_target_code = target_labels[selected_target_label]

with select_col_2:
    selected_source_label = st.selectbox(
        "Mission historique utilisée comme référence",
        options=list(historical_labels.keys()),
        index=default_source_index,
    )
    selected_source_code = historical_labels[selected_source_label]

target_info = target_df[target_df["mission_code"] == selected_target_code].iloc[0]
source_info = historical_df[historical_df["mission_code"] == selected_source_code].iloc[0]

preview_left, preview_right = st.columns(2)

with preview_left:
    st.markdown(
        f"""
        <div class="panel-box">
            <b>Mission cible :</b> {target_info['mission_code']}<br>
            <span class="small-muted">{target_info['mission_title']}</span><br>
            <span class="small-muted">Client : {target_info['client_name']} · Site : {target_info['site_name']} · Audit : {fmt_date(target_info['planned_audit_date'])}</span>
        </div>
        """,
        unsafe_allow_html=True,
    )

with preview_right:
    st.markdown(
        f"""
        <div class="panel-box">
            <b>Mission historique :</b> {source_info['mission_code']}<br>
            <span class="small-muted">{source_info['mission_title']}</span><br>
            <span class="small-muted">Constats exploitables : {safe_int(source_info['findings_count'])} · Audit : {fmt_date(source_info['planned_audit_date'])}</span>
        </div>
        """,
        unsafe_allow_html=True,
    )

same_mission_selected = selected_target_code == selected_source_code

if same_mission_selected:
    st.warning(
        "La mission cible et la mission historique sont identiques. "
        "Sélectionne une mission historique distincte pour produire une préparation réellement pertinente."
    )

generate_clicked = st.button(
    "Générer / régénérer la check-list priorisée",
    type="primary",
    use_container_width=True,
    disabled=same_mission_selected,
)

if generate_clicked:
    try:
        with st.spinner("Exécution du moteur SQL multi-mission..."):
            generation_result, refreshed_ref = generate_and_refresh(
                host=host,
                port=int(port),
                dbname=dbname,
                user=user,
                password=password,
                target_mission_code=selected_target_code,
                source_mission_code=selected_source_code,
            )

        st.session_state.reference_data = refreshed_ref
        st.session_state.selected_source_code = selected_source_code
        st.session_state.selected_target_code = selected_target_code
        st.session_state.last_generation_result = generation_result

        if not generation_result.empty:
            result = generation_result.iloc[0]
            batch_code = result["generation_batch_code"]
            st.session_state.selected_batch_code = batch_code
            st.success(
                f"Check-list générée : {safe_int(result['recommendations_count'])} recommandations "
                f"et {safe_int(result['checklist_items_count'])} questions. "
                "Le nouveau lot est automatiquement sélectionné ci-dessous."
            )
        else:
            st.warning("La fonction de génération s’est exécutée, mais aucun résultat n’a été retourné.")

        runs_df = refreshed_ref["runs"].copy()

    except Exception as exc:
        st.error("La génération a échoué.")
        st.code(str(exc))


# ============================================================
# 8. CHOIX D'UN LOT EXISTANT
# ============================================================

st.divider()
st.subheader("2. Sélectionner un lot de génération à visualiser")
st.markdown(
    '<div class="section-caption">Tu peux consulter le dernier lot généré ou revenir sur un lot déjà présent dans l’historique.</div>',
    unsafe_allow_html=True,
)

runs_df = st.session_state.reference_data["runs"].copy()

if runs_df.empty:
    st.warning("Aucun lot de génération disponible. Lance d’abord une génération ci-dessus.")
    st.stop()

run_labels = {run_label(row): row["generation_batch_code"] for _, row in runs_df.iterrows()}

if st.session_state.selected_batch_code in run_labels.values():
    default_run_index = list(run_labels.values()).index(st.session_state.selected_batch_code)
else:
    default_run_index = 0

selected_run_label = st.selectbox(
    "Lot à afficher",
    options=list(run_labels.keys()),
    index=default_run_index,
)

selected_batch_code = run_labels[selected_run_label]
selected_run = runs_df[runs_df["generation_batch_code"] == selected_batch_code].iloc[0]

st.session_state.selected_batch_code = selected_batch_code
st.session_state.selected_source_code = selected_run["source_mission_code"]
st.session_state.selected_target_code = selected_run["target_mission_code"]

st.markdown(
    f"""
    <div class="panel-box">
        <span class="run-badge">Lot sélectionné</span><br><br>
        <b>{selected_run['generation_batch_code']}</b><br>
        <span class="small-muted">
            Cible : {selected_run['target_mission_code']} · Historique : {selected_run['source_mission_code']} ·
            Questions : {safe_int(selected_run['checklist_items_count'])} ·
            Généré le : {fmt_datetime(selected_run['generated_at'])}
        </span>
    </div>
    """,
    unsafe_allow_html=True,
)


# ============================================================
# 9. CHARGER LES DONNÉES DU LOT
# ============================================================

try:
    with st.spinner("Chargement du dashboard du lot sélectionné..."):
        dashboard_data = load_run_dashboard_data(
            host=host,
            port=int(port),
            dbname=dbname,
            user=user,
            password=password,
            generation_batch_code=selected_batch_code,
            source_mission_code=str(selected_run["source_mission_code"]),
            target_mission_code=str(selected_run["target_mission_code"]),
        )
    st.session_state.dashboard_data = dashboard_data
except Exception as exc:
    st.error("Impossible de charger les données du lot sélectionné.")
    st.code(str(exc))
    st.stop()

dashboard_data = st.session_state.dashboard_data

target_mission_df = dashboard_data["target_mission"]
kpi_df = dashboard_data["kpis"]
alerts_df = dashboard_data["alerts"]
process_df = dashboard_data["process"]
clause_df = dashboard_data["clause"]
checklist_df = dashboard_data["checklist"]
recommendations_df = dashboard_data["recommendations"]

if target_mission_df.empty:
    st.error("Les détails de la mission cible sont introuvables.")
    st.stop()

mission = target_mission_df.iloc[0]
kpi_counts = priority_counts(kpi_df)

st.markdown(
    f"""
    <div class="mission-box">
        <div class="mission-title">{mission['mission_title']}</div>
        <div class="meta-line">
            <b>Code mission :</b> {mission['mission_code']} &nbsp; | &nbsp;
            <b>Client :</b> {mission['client_name']} &nbsp; | &nbsp;
            <b>Site :</b> {mission['site_name']}
        </div>
        <div class="meta-line">
            <b>Date prévue :</b> {fmt_date(mission['planned_audit_date'])} &nbsp; | &nbsp;
            <b>Référentiel :</b> {mission['standard_label']}
        </div>
        <div class="small-muted">
            Score de vigilance calculé depuis la mission historique <b>{selected_run['source_mission_code']}</b>.
        </div>
    </div>
    """,
    unsafe_allow_html=True,
)


# ============================================================
# 10. ONGLETS DE RESTITUTION
# ============================================================

tab1, tab2, tab3, tab4 = st.tabs(
    [
        "3. Vue exécutive",
        "4. Vigilance métier",
        "5. Check-list IA",
        "6. Traçabilité",
    ]
)


# ============================================================
# TAB 1 – VUE EXÉCUTIVE
# ============================================================

with tab1:
    st.subheader("Synthèse de la génération")
    st.markdown(
        '<div class="section-caption">Vue globale du lot sélectionné et des priorités de préparation produites automatiquement.</div>',
        unsafe_allow_html=True,
    )

    k1, k2, k3, k4 = st.columns(4)
    k1.metric("Questions générées", len(checklist_df))
    k2.metric("Priorité haute", kpi_counts["Haute"])
    k3.metric("Priorité moyenne", kpi_counts["Moyenne"])
    k4.metric("Priorité faible", kpi_counts["Faible"])

    st.subheader("Alertes prioritaires liées à l’historique")
    alert_left, alert_right = st.columns([1.45, 1])

    with alert_left:
        if alerts_df.empty:
            st.info("Aucune alerte disponible.")
        else:
            for _, row in alerts_df.head(5).iterrows():
                st.markdown(
                    f"""
                    <div class="alert-card">
                        <div class="alert-title">
                            {row['alert_dimension']} – {row['alert_label']}
                            {pill(str(row['vigilance_level']))}
                        </div>
                        <div class="small-muted">
                            Score de vigilance : <b>{safe_float(row['capped_score']):.0f}/100</b>
                        </div>
                    </div>
                    """,
                    unsafe_allow_html=True,
                )

    with alert_right:
        st.markdown("#### Décisions immédiates")
        top_process = alerts_df[alerts_df["alert_dimension"] == "Processus"].head(1)
        top_clause = alerts_df[alerts_df["alert_dimension"] == "Clause ISO"].head(1)

        if not top_process.empty:
            row = top_process.iloc[0]
            st.metric(
                "Processus le plus sensible",
                str(row["alert_label"]),
                f"{safe_float(row['capped_score']):.0f}/100",
            )

        if not top_clause.empty:
            row = top_clause.iloc[0]
            st.metric(
                "Clause ISO la plus sensible",
                str(row["alert_key"]),
                f"{safe_float(row['capped_score']):.0f}/100",
            )

    st.markdown(
        """
        <div class="note-box">
            <b>Lecture métier :</b> ce lot traduit automatiquement les signaux de l’audit historique
            en une préparation ciblée de la mission sélectionnée. Les scores de vigilance structurent
            les priorités de la check-list.
        </div>
        """,
        unsafe_allow_html=True,
    )


# ============================================================
# TAB 2 – VIGILANCE MÉTIER
# ============================================================

with tab2:
    st.subheader("Vigilance métier issue de la mission historique")
    st.markdown(
        '<div class="section-caption">Analyse des risques de préparation par processus métier et par clause ISO.</div>',
        unsafe_allow_html=True,
    )

    col1, col2 = st.columns(2)

    with col1:
        st.markdown("#### Par processus")
        process_table = process_df[
            [
                "process_name",
                "findings_count",
                "nonconformities_count",
                "remarks_count",
                "improvements_count",
                "capped_score",
                "vigilance_level",
            ]
        ].rename(
            columns={
                "process_name": "Processus",
                "findings_count": "Constats",
                "nonconformities_count": "NC",
                "remarks_count": "RQ",
                "improvements_count": "AM",
                "capped_score": "Score",
                "vigilance_level": "Niveau",
            }
        )
        st.dataframe(process_table, use_container_width=True, hide_index=True, height=300)

        if not process_df.empty:
            st.bar_chart(
                process_df[["process_name", "capped_score"]].set_index("process_name"),
                height=280,
            )

    with col2:
        st.markdown("#### Par clause ISO")
        clause_table = clause_df[
            [
                "clause_code",
                "clause_title",
                "findings_count",
                "capped_score",
                "vigilance_level",
            ]
        ].rename(
            columns={
                "clause_code": "Clause",
                "clause_title": "Intitulé",
                "findings_count": "Constats",
                "capped_score": "Score",
                "vigilance_level": "Niveau",
            }
        )
        st.dataframe(clause_table, use_container_width=True, hide_index=True, height=300)

        if not clause_df.empty:
            st.bar_chart(
                clause_df[["clause_code", "capped_score"]].set_index("clause_code"),
                height=280,
            )

    with st.expander("Voir les explications détaillées des scores"):
        p_exp = process_df[
            ["process_name", "capped_score", "vigilance_level", "explanation_summary"]
        ].rename(
            columns={
                "process_name": "Processus",
                "capped_score": "Score",
                "vigilance_level": "Niveau",
                "explanation_summary": "Explication",
            }
        )
        st.markdown("##### Processus")
        st.dataframe(p_exp, use_container_width=True, hide_index=True, height=260)

        c_exp = clause_df[
            ["clause_code", "clause_title", "capped_score", "vigilance_level", "explanation_summary"]
        ].rename(
            columns={
                "clause_code": "Clause",
                "clause_title": "Intitulé",
                "capped_score": "Score",
                "vigilance_level": "Niveau",
                "explanation_summary": "Explication",
            }
        )
        st.markdown("##### Clauses ISO")
        st.dataframe(c_exp, use_container_width=True, hide_index=True, height=300)


# ============================================================
# TAB 3 – CHECK-LIST IA
# ============================================================

with tab3:
    st.subheader("Check-list priorisée produite par le moteur")
    st.markdown(
        '<div class="section-caption">Filtre, recherche et export du livrable de préparation d’audit.</div>',
        unsafe_allow_html=True,
    )

    f1, f2 = st.columns([1.1, 1])

    with f1:
        selected_priorities = st.multiselect(
            "Filtrer par priorité",
            options=["Haute", "Moyenne", "Faible"],
            default=["Haute", "Moyenne", "Faible"],
        )

    with f2:
        search = st.text_input(
            "Rechercher dans la check-list",
            value="",
            placeholder="Ex. compétences, non-conformité, procédures...",
        )

    filtered = checklist_df.copy()

    if selected_priorities:
        filtered = filtered[filtered["generated_priority"].isin(selected_priorities)]

    if search.strip():
        mask = (
            filtered["question_text"].fillna("").str.contains(search, case=False, regex=False)
            | filtered["clause_title"].fillna("").str.contains(search, case=False, regex=False)
            | filtered["theme"].fillna("").str.contains(search, case=False, regex=False)
        )
        filtered = filtered[mask]

    display_df = filtered[
        [
            "display_order",
            "clause_code",
            "clause_title",
            "theme",
            "question_text",
            "generated_priority",
            "recommendation_label",
            "conformity_status",
        ]
    ].rename(
        columns={
            "display_order": "Ordre",
            "clause_code": "Clause",
            "clause_title": "Intitulé clause",
            "theme": "Thème",
            "question_text": "Question d’audit",
            "generated_priority": "Priorité",
            "recommendation_label": "Recommandation",
            "conformity_status": "Statut",
        }
    )

    if display_df.empty:
        st.warning("Aucune question ne correspond aux filtres.")
    else:
        styled = display_df.style.map(style_priority, subset=["Priorité"])
        st.dataframe(styled, use_container_width=True, hide_index=True, height=540)

    csv_bytes = display_df.to_csv(index=False).encode("utf-8-sig")
    st.download_button(
        "Télécharger la check-list filtrée en CSV",
        data=csv_bytes,
        file_name="auditprep_checklist_multi_mission.csv",
        mime="text/csv",
    )

    with st.expander("Voir les justifications détaillées"):
        justif_df = filtered[
            [
                "display_order",
                "clause_code",
                "question_text",
                "generated_priority",
                "expected_evidence",
            ]
        ].rename(
            columns={
                "display_order": "Ordre",
                "clause_code": "Clause",
                "question_text": "Question",
                "generated_priority": "Priorité",
                "expected_evidence": "Preuves attendues et justification",
            }
        )
        st.dataframe(justif_df, use_container_width=True, hide_index=True, height=430)


# ============================================================
# TAB 4 – TRAÇABILITÉ
# ============================================================

with tab4:
    st.subheader("Traçabilité du raisonnement de priorisation")
    st.markdown(
        '<div class="section-caption">Lien explicite entre scores, règle de priorité et question générée.</div>',
        unsafe_allow_html=True,
    )

    rec_df = recommendations_df.rename(
        columns={
            "clause_code": "Clause",
            "clause_title": "Intitulé clause",
            "process_name": "Processus",
            "source_dimension": "Source",
            "question_template": "Question",
            "clause_vigilance_score": "Score clause",
            "process_vigilance_score": "Score processus",
            "retained_score": "Score retenu",
            "generated_priority": "Priorité",
            "recommendation_label": "Recommandation",
            "prioritization_reason": "Justification",
        }
    )

    st.dataframe(rec_df, use_container_width=True, hide_index=True, height=580)

    st.markdown(
        """
        <div class="note-box">
            <b>Principe :</b> le moteur prend en compte la vigilance associée à la clause ISO,
            la vigilance associée au processus métier, conserve le signal le plus fort,
            puis applique une règle explicable de priorisation.
        </div>
        """,
        unsafe_allow_html=True,
    )


# ============================================================
# 11. EXPORT GLOBAL
# ============================================================

st.divider()
st.subheader("7. Exports du lot sélectionné")
st.markdown(
    '<div class="section-caption">Téléchargement des données consolidées pour l’entreprise, le rapport PFE ou une démonstration.</div>',
    unsafe_allow_html=True,
)

excel_bytes = build_excel_export(
    {
        "Lot sélectionné": pd.DataFrame([selected_run]),
        "Mission cible": target_mission_df,
        "KPI priorités": kpi_df,
        "Alertes": alerts_df,
        "Vigilance processus": process_df,
        "Vigilance clauses": clause_df,
        "Check-list IA": checklist_df,
        "Traçabilité": recommendations_df,
    }
)

export_col_1, export_col_2 = st.columns([1, 2])

with export_col_1:
    st.download_button(
        "Télécharger l’export Excel complet",
        data=excel_bytes,
        file_name="auditprep_export_multi_mission.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        type="primary",
        use_container_width=True,
    )

with export_col_2:
    st.info(
        "L’export contient le lot de génération, la mission cible, les KPI, les alertes, "
        "les scores de vigilance, la check-list produite et la traçabilité du raisonnement."
    )

st.caption("Prototype PFE – AuditPrep IA | Dashboard Streamlit V3.1 multi-mission sécurisée | Moteur SQL V6")
