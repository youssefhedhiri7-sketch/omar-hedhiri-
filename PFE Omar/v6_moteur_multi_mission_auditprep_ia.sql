-- ============================================================
-- PROJET : AuditPrep IA
-- SCRIPT : SQL V6 - Moteur multi-mission réutilisable
-- OBJECTIF :
--   1) Calculer les scores de vigilance pour n'importe quelle mission historique ;
--   2) Générer une check-list priorisée pour n'importe quelle mission cible ;
--   3) Ne plus dépendre de codes de mission écrits en dur dans le moteur SQL ;
--   4) Préparer le Dashboard V3 avec sélection dynamique des missions.
--
-- PRÉREQUIS :
-- - Scripts V1, V1.5/V2, V3, V4, V5 déjà exécutés
-- - Schéma : auditprep
--
-- IMPORTANT :
-- - Ce script ne supprime pas les données métier existantes.
-- - Il ajoute des fonctions SQL réutilisables.
-- - Les fonctions peuvent être relancées plusieurs fois proprement.
-- ============================================================

SET search_path TO auditprep;

-- ============================================================
-- 1. COMPLÉMENT : TABLE DE JOURNALISATION DES GÉNÉRATIONS
-- ============================================================

CREATE TABLE IF NOT EXISTS smart_checklist_generation_runs (
    generation_run_id SERIAL PRIMARY KEY,
    source_mission_id INT NOT NULL REFERENCES audit_missions(mission_id) ON DELETE CASCADE,
    target_mission_id INT NOT NULL REFERENCES audit_missions(mission_id) ON DELETE CASCADE,
    generation_batch_code VARCHAR(120) NOT NULL UNIQUE,
    generated_checklist_title VARCHAR(255) NOT NULL,
    clause_scores_recomputed BOOLEAN NOT NULL DEFAULT FALSE,
    process_scores_recomputed BOOLEAN NOT NULL DEFAULT FALSE,
    recommendations_count INT NOT NULL DEFAULT 0 CHECK (recommendations_count >= 0),
    checklist_items_count INT NOT NULL DEFAULT 0 CHECK (checklist_items_count >= 0),
    generated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_generation_runs_source_mission
    ON smart_checklist_generation_runs(source_mission_id);

CREATE INDEX IF NOT EXISTS idx_generation_runs_target_mission
    ON smart_checklist_generation_runs(target_mission_id);

CREATE INDEX IF NOT EXISTS idx_generation_runs_generated_at
    ON smart_checklist_generation_runs(generated_at DESC);


-- ============================================================
-- 2. VUE : LISTE DES MISSIONS UTILISABLES PAR LE DASHBOARD
-- ============================================================
-- Une mission historique est une mission ayant au moins un rapport d'audit et un constat.
-- Une mission cible est une mission disposant d'un code mission et pouvant recevoir une checklist.

CREATE OR REPLACE VIEW vw_available_historical_missions AS
SELECT DISTINCT
    am.mission_id,
    am.mission_code,
    am.mission_title,
    COALESCE(c.client_name, am.client_name) AS client_name,
    COALESCE(cs.site_name, 'Site non renseigné') AS site_name,
    am.planned_audit_date,
    COUNT(DISTINCT af.finding_id) AS findings_count
FROM audit_missions am
JOIN audit_reports ar ON ar.mission_id = am.mission_id
JOIN audit_findings af ON af.audit_report_id = ar.audit_report_id
LEFT JOIN clients c ON c.client_id = am.client_id
LEFT JOIN client_sites cs ON cs.site_id = am.site_id
GROUP BY
    am.mission_id,
    am.mission_code,
    am.mission_title,
    COALESCE(c.client_name, am.client_name),
    COALESCE(cs.site_name, 'Site non renseigné'),
    am.planned_audit_date
ORDER BY am.planned_audit_date DESC NULLS LAST, am.mission_code;


CREATE OR REPLACE VIEW vw_available_target_missions AS
SELECT
    am.mission_id,
    am.mission_code,
    am.mission_title,
    COALESCE(c.client_name, am.client_name) AS client_name,
    COALESCE(cs.site_name, 'Site non renseigné') AS site_name,
    am.planned_audit_date,
    COALESCE(s.standard_code || ':' || s.standard_version, 'Référentiel non renseigné') AS standard_label
FROM audit_missions am
LEFT JOIN clients c ON c.client_id = am.client_id
LEFT JOIN client_sites cs ON cs.site_id = am.site_id
LEFT JOIN standards s ON s.standard_id = am.primary_standard_id
ORDER BY am.planned_audit_date DESC NULLS LAST, am.mission_code;


-- ============================================================
-- 3. FONCTION : RECALCULER LA VIGILANCE PAR CLAUSE
-- ============================================================

CREATE OR REPLACE FUNCTION fn_recompute_clause_vigilance_scores(
    p_source_mission_code VARCHAR
)
RETURNS TABLE (
    source_mission_code VARCHAR,
    inserted_rows INT
)
LANGUAGE plpgsql
AS $$
DECLARE
    v_source_mission_id INT;
    v_inserted_rows INT := 0;
BEGIN
    SELECT mission_id
    INTO v_source_mission_id
    FROM audit_missions
    WHERE mission_code = p_source_mission_code
    LIMIT 1;

    IF v_source_mission_id IS NULL THEN
        RAISE EXCEPTION 'Mission historique introuvable : %', p_source_mission_code;
    END IF;

    IF NOT EXISTS (
        SELECT 1
        FROM audit_reports ar
        JOIN audit_findings af ON af.audit_report_id = ar.audit_report_id
        WHERE ar.mission_id = v_source_mission_id
    ) THEN
        RAISE EXCEPTION 'La mission % ne possède aucun constat exploitable.', p_source_mission_code;
    END IF;

    DELETE FROM clause_vigilance_scores
    WHERE mission_id = v_source_mission_id;

    WITH active_rules AS (
        SELECT
            MAX(CASE WHEN rule_code = 'WEIGHT_NC' THEN weight_value END) AS weight_nc,
            MAX(CASE WHEN rule_code = 'WEIGHT_RQ' THEN weight_value END) AS weight_rq,
            MAX(CASE WHEN rule_code = 'WEIGHT_AM' THEN weight_value END) AS weight_am,
            MAX(CASE WHEN rule_code = 'BONUS_REPEAT_CLAUSE' THEN weight_value END) AS bonus_repeat_clause,
            MAX(CASE WHEN rule_code = 'WEIGHT_OPEN_CORRECTIVE_ACTION' THEN weight_value END) AS weight_open_action
        FROM vigilance_scoring_rules
        WHERE is_active = TRUE
    ),
    clause_findings AS (
        SELECT
            am.mission_id,
            af.clause_id,
            COUNT(*) AS findings_count,
            COUNT(*) FILTER (WHERE ft.code = 'NC') AS nonconformities_count,
            COUNT(*) FILTER (WHERE ft.code = 'RQ') AS remarks_count,
            COUNT(*) FILTER (WHERE ft.code = 'AM') AS improvements_count
        FROM audit_findings af
        JOIN audit_reports ar ON ar.audit_report_id = af.audit_report_id
        JOIN audit_missions am ON am.mission_id = ar.mission_id
        JOIN finding_types ft ON ft.finding_type_id = af.finding_type_id
        WHERE am.mission_id = v_source_mission_id
        GROUP BY am.mission_id, af.clause_id
    ),
    clause_open_actions AS (
        SELECT
            am.mission_id,
            af.clause_id,
            COUNT(ca.corrective_action_id) FILTER (
                WHERE ca.action_status IN ('Envisagée', 'Planifiée')
            ) AS open_corrective_actions_count
        FROM audit_findings af
        JOIN audit_reports ar ON ar.audit_report_id = af.audit_report_id
        JOIN audit_missions am ON am.mission_id = ar.mission_id
        LEFT JOIN nonconformities nc ON nc.finding_id = af.finding_id
        LEFT JOIN corrective_actions ca ON ca.nonconformity_id = nc.nonconformity_id
        WHERE am.mission_id = v_source_mission_id
        GROUP BY am.mission_id, af.clause_id
    ),
    scoring_base AS (
        SELECT
            cf.mission_id,
            cf.clause_id,
            cf.findings_count,
            cf.nonconformities_count,
            cf.remarks_count,
            cf.improvements_count,
            COALESCE(coa.open_corrective_actions_count, 0) AS open_corrective_actions_count,
            (
                cf.nonconformities_count * ar.weight_nc
                + cf.remarks_count * ar.weight_rq
                + cf.improvements_count * ar.weight_am
                + CASE WHEN cf.findings_count >= 2 THEN ar.bonus_repeat_clause ELSE 0 END
                + COALESCE(coa.open_corrective_actions_count, 0) * ar.weight_open_action
            ) AS raw_score
        FROM clause_findings cf
        CROSS JOIN active_rules ar
        LEFT JOIN clause_open_actions coa
            ON coa.mission_id = cf.mission_id
           AND coa.clause_id IS NOT DISTINCT FROM cf.clause_id
    )
    INSERT INTO clause_vigilance_scores (
        mission_id,
        clause_id,
        findings_count,
        nonconformities_count,
        remarks_count,
        improvements_count,
        open_corrective_actions_count,
        raw_score,
        capped_score,
        risk_level_id,
        explanation_summary
    )
    SELECT
        sb.mission_id,
        sb.clause_id,
        sb.findings_count,
        sb.nonconformities_count,
        sb.remarks_count,
        sb.improvements_count,
        sb.open_corrective_actions_count,
        sb.raw_score,
        LEAST(sb.raw_score, 100.00) AS capped_score,
        rl.risk_level_id,
        CONCAT(
            'Score calculé à partir de ', sb.findings_count, ' constat(s) : ',
            sb.nonconformities_count, ' NC, ',
            sb.remarks_count, ' remarque(s), ',
            sb.improvements_count, ' amélioration(s), ',
            sb.open_corrective_actions_count, ' action(s) corrective(s) non finalisée(s).'
        ) AS explanation_summary
    FROM scoring_base sb
    LEFT JOIN risk_levels rl
        ON LEAST(sb.raw_score, 100.00) BETWEEN rl.min_score AND rl.max_score;

    GET DIAGNOSTICS v_inserted_rows = ROW_COUNT;

    RETURN QUERY
    SELECT p_source_mission_code, v_inserted_rows;
END;
$$;


-- ============================================================
-- 4. FONCTION : RECALCULER LA VIGILANCE PAR PROCESSUS
-- ============================================================

CREATE OR REPLACE FUNCTION fn_recompute_process_vigilance_scores(
    p_source_mission_code VARCHAR
)
RETURNS TABLE (
    source_mission_code VARCHAR,
    inserted_rows INT
)
LANGUAGE plpgsql
AS $$
DECLARE
    v_source_mission_id INT;
    v_inserted_rows INT := 0;
BEGIN
    SELECT mission_id
    INTO v_source_mission_id
    FROM audit_missions
    WHERE mission_code = p_source_mission_code
    LIMIT 1;

    IF v_source_mission_id IS NULL THEN
        RAISE EXCEPTION 'Mission historique introuvable : %', p_source_mission_code;
    END IF;

    IF NOT EXISTS (
        SELECT 1
        FROM audit_reports ar
        JOIN audit_findings af ON af.audit_report_id = ar.audit_report_id
        WHERE ar.mission_id = v_source_mission_id
    ) THEN
        RAISE EXCEPTION 'La mission % ne possède aucun constat exploitable.', p_source_mission_code;
    END IF;

    DELETE FROM process_vigilance_scores
    WHERE mission_id = v_source_mission_id;

    WITH active_rules AS (
        SELECT
            MAX(CASE WHEN rule_code = 'WEIGHT_NC' THEN weight_value END) AS weight_nc,
            MAX(CASE WHEN rule_code = 'WEIGHT_RQ' THEN weight_value END) AS weight_rq,
            MAX(CASE WHEN rule_code = 'WEIGHT_AM' THEN weight_value END) AS weight_am,
            MAX(CASE WHEN rule_code = 'BONUS_REPEAT_PROCESS' THEN weight_value END) AS bonus_repeat_process,
            MAX(CASE WHEN rule_code = 'WEIGHT_OPEN_CORRECTIVE_ACTION' THEN weight_value END) AS weight_open_action
        FROM vigilance_scoring_rules
        WHERE is_active = TRUE
    ),
    process_findings AS (
        SELECT
            am.mission_id,
            af.process_id,
            COUNT(*) AS findings_count,
            COUNT(*) FILTER (WHERE ft.code = 'NC') AS nonconformities_count,
            COUNT(*) FILTER (WHERE ft.code = 'RQ') AS remarks_count,
            COUNT(*) FILTER (WHERE ft.code = 'AM') AS improvements_count
        FROM audit_findings af
        JOIN audit_reports ar ON ar.audit_report_id = af.audit_report_id
        JOIN audit_missions am ON am.mission_id = ar.mission_id
        JOIN finding_types ft ON ft.finding_type_id = af.finding_type_id
        WHERE am.mission_id = v_source_mission_id
        GROUP BY am.mission_id, af.process_id
    ),
    process_open_actions AS (
        SELECT
            am.mission_id,
            af.process_id,
            COUNT(ca.corrective_action_id) FILTER (
                WHERE ca.action_status IN ('Envisagée', 'Planifiée')
            ) AS open_corrective_actions_count
        FROM audit_findings af
        JOIN audit_reports ar ON ar.audit_report_id = af.audit_report_id
        JOIN audit_missions am ON am.mission_id = ar.mission_id
        LEFT JOIN nonconformities nc ON nc.finding_id = af.finding_id
        LEFT JOIN corrective_actions ca ON ca.nonconformity_id = nc.nonconformity_id
        WHERE am.mission_id = v_source_mission_id
        GROUP BY am.mission_id, af.process_id
    ),
    scoring_base AS (
        SELECT
            pf.mission_id,
            pf.process_id,
            pf.findings_count,
            pf.nonconformities_count,
            pf.remarks_count,
            pf.improvements_count,
            COALESCE(poa.open_corrective_actions_count, 0) AS open_corrective_actions_count,
            (
                pf.nonconformities_count * ar.weight_nc
                + pf.remarks_count * ar.weight_rq
                + pf.improvements_count * ar.weight_am
                + CASE WHEN pf.findings_count >= 2 THEN ar.bonus_repeat_process ELSE 0 END
                + COALESCE(poa.open_corrective_actions_count, 0) * ar.weight_open_action
            ) AS raw_score
        FROM process_findings pf
        CROSS JOIN active_rules ar
        LEFT JOIN process_open_actions poa
            ON poa.mission_id = pf.mission_id
           AND poa.process_id IS NOT DISTINCT FROM pf.process_id
    )
    INSERT INTO process_vigilance_scores (
        mission_id,
        process_id,
        findings_count,
        nonconformities_count,
        remarks_count,
        improvements_count,
        open_corrective_actions_count,
        raw_score,
        capped_score,
        risk_level_id,
        explanation_summary
    )
    SELECT
        sb.mission_id,
        sb.process_id,
        sb.findings_count,
        sb.nonconformities_count,
        sb.remarks_count,
        sb.improvements_count,
        sb.open_corrective_actions_count,
        sb.raw_score,
        LEAST(sb.raw_score, 100.00) AS capped_score,
        rl.risk_level_id,
        CONCAT(
            'Score calculé à partir de ', sb.findings_count, ' constat(s) : ',
            sb.nonconformities_count, ' NC, ',
            sb.remarks_count, ' remarque(s), ',
            sb.improvements_count, ' amélioration(s), ',
            sb.open_corrective_actions_count, ' action(s) corrective(s) non finalisée(s).'
        ) AS explanation_summary
    FROM scoring_base sb
    LEFT JOIN risk_levels rl
        ON LEAST(sb.raw_score, 100.00) BETWEEN rl.min_score AND rl.max_score;

    GET DIAGNOSTICS v_inserted_rows = ROW_COUNT;

    RETURN QUERY
    SELECT p_source_mission_code, v_inserted_rows;
END;
$$;


-- ============================================================
-- 5. FONCTION : RECALCULER TOUTES LES VIGILANCES
-- ============================================================

CREATE OR REPLACE FUNCTION fn_recompute_all_vigilance_scores(
    p_source_mission_code VARCHAR
)
RETURNS TABLE (
    source_mission_code VARCHAR,
    clause_rows INT,
    process_rows INT
)
LANGUAGE plpgsql
AS $$
DECLARE
    v_clause_rows INT := 0;
    v_process_rows INT := 0;
BEGIN
    SELECT inserted_rows
    INTO v_clause_rows
    FROM fn_recompute_clause_vigilance_scores(p_source_mission_code);

    SELECT inserted_rows
    INTO v_process_rows
    FROM fn_recompute_process_vigilance_scores(p_source_mission_code);

    RETURN QUERY
    SELECT p_source_mission_code, COALESCE(v_clause_rows, 0), COALESCE(v_process_rows, 0);
END;
$$;


-- ============================================================
-- 6. FONCTION : GÉNÉRER UNE CHECK-LIST PRIORISÉE MULTI-MISSION
-- ============================================================
-- Paramètres :
--   p_target_mission_code : mission à préparer
--   p_source_mission_code : mission historique utilisée pour la vigilance
--
-- La fonction :
--   - recalcule les scores de vigilance de la mission source ;
--   - supprime l'ancien lot strictement équivalent s'il existe ;
--   - produit les recommandations ;
--   - crée une checklist priorisée ;
--   - insère les items de checklist ;
--   - journalise le run.
-- ============================================================

CREATE OR REPLACE FUNCTION fn_generate_smart_checklist(
    p_target_mission_code VARCHAR,
    p_source_mission_code VARCHAR
)
RETURNS TABLE (
    generation_batch_code VARCHAR,
    generated_checklist_title VARCHAR,
    recommendations_count INT,
    checklist_items_count INT
)
LANGUAGE plpgsql
AS $$
DECLARE
    v_target_mission_id INT;
    v_source_mission_id INT;
    v_batch_code VARCHAR(120);
    v_checklist_title VARCHAR(255);
    v_checklist_id INT;
    v_recommendations_count INT := 0;
    v_checklist_items_count INT := 0;
    v_clause_rows INT := 0;
    v_process_rows INT := 0;
BEGIN
    SELECT mission_id
    INTO v_target_mission_id
    FROM audit_missions
    WHERE mission_code = p_target_mission_code
    LIMIT 1;

    IF v_target_mission_id IS NULL THEN
        RAISE EXCEPTION 'Mission cible introuvable : %', p_target_mission_code;
    END IF;

    SELECT mission_id
    INTO v_source_mission_id
    FROM audit_missions
    WHERE mission_code = p_source_mission_code
    LIMIT 1;

    IF v_source_mission_id IS NULL THEN
        RAISE EXCEPTION 'Mission historique introuvable : %', p_source_mission_code;
    END IF;

    IF NOT EXISTS (
        SELECT 1
        FROM audit_reports ar
        JOIN audit_findings af ON af.audit_report_id = ar.audit_report_id
        WHERE ar.mission_id = v_source_mission_id
    ) THEN
        RAISE EXCEPTION 'La mission historique % ne possède aucun constat exploitable.', p_source_mission_code;
    END IF;

    -- 6.1 Recalcul des scores source
    SELECT clause_rows, process_rows
    INTO v_clause_rows, v_process_rows
    FROM fn_recompute_all_vigilance_scores(p_source_mission_code);

    -- 6.2 Construction d'un code de lot stable et lisible
    v_batch_code := 'SMART_' ||
                    REGEXP_REPLACE(UPPER(p_target_mission_code), '[^A-Z0-9]+', '_', 'g') ||
                    '_FROM_' ||
                    REGEXP_REPLACE(UPPER(p_source_mission_code), '[^A-Z0-9]+', '_', 'g') ||
                    '_V2';

    v_checklist_title := 'Check-list priorisée AuditPrep IA – ' ||
                         p_target_mission_code ||
                         ' – source ' ||
                         p_source_mission_code;

    -- 6.3 Nettoyage du même lot si relance
    DELETE FROM checklist_items ci
    USING checklists c
    WHERE ci.checklist_id = c.checklist_id
      AND c.mission_id = v_target_mission_id
      AND c.checklist_title = v_checklist_title;

    DELETE FROM checklists
    WHERE mission_id = v_target_mission_id
      AND checklist_title = v_checklist_title;

    DELETE FROM checklist_prioritization_recommendations
    WHERE target_mission_id = v_target_mission_id
      AND source_mission_id = v_source_mission_id
      AND generation_batch_code = v_batch_code;

    DELETE FROM smart_checklist_generation_runs
    WHERE generation_batch_code = v_batch_code;

    -- 6.4 Génération des recommandations
    WITH repository_points AS (
        SELECT
            cpr.repository_point_id,
            cpr.clause_id,
            cpr.process_id,
            cpr.question_template,
            cpr.requirement_text
        FROM control_points_repository cpr
        JOIN audit_types at ON at.audit_type_id = cpr.audit_type_id
        WHERE cpr.is_active = TRUE
          AND at.label = 'Audit interne'
    ),
    clause_scores AS (
        SELECT
            cvs.clause_id,
            cvs.capped_score AS clause_vigilance_score
        FROM clause_vigilance_scores cvs
        WHERE cvs.mission_id = v_source_mission_id
    ),
    process_scores AS (
        SELECT
            pvs.process_id,
            pvs.capped_score AS process_vigilance_score
        FROM process_vigilance_scores pvs
        WHERE pvs.mission_id = v_source_mission_id
    ),
    scored_points AS (
        SELECT
            rp.repository_point_id,
            rp.clause_id,
            rp.process_id,
            cs.clause_vigilance_score,
            ps.process_vigilance_score,
            GREATEST(
                COALESCE(cs.clause_vigilance_score, 0),
                COALESCE(ps.process_vigilance_score, 0)
            ) AS retained_score,
            CASE
                WHEN cs.clause_vigilance_score IS NOT NULL
                 AND ps.process_vigilance_score IS NOT NULL THEN 'Mixte'
                WHEN cs.clause_vigilance_score IS NOT NULL THEN 'Clause ISO'
                WHEN ps.process_vigilance_score IS NOT NULL THEN 'Processus'
                ELSE 'Clause ISO'
            END AS source_dimension
        FROM repository_points rp
        LEFT JOIN clause_scores cs
            ON cs.clause_id IS NOT DISTINCT FROM rp.clause_id
        LEFT JOIN process_scores ps
            ON ps.process_id IS NOT DISTINCT FROM rp.process_id
    ),
    points_with_rules AS (
        SELECT
            sp.*,
            cprule.generated_priority_label,
            cprule.recommendation_label,
            cprule.explanation_template
        FROM scored_points sp
        JOIN checklist_prioritization_rules cprule
            ON sp.retained_score BETWEEN cprule.min_score AND cprule.max_score
           AND cprule.is_active = TRUE
    )
    INSERT INTO checklist_prioritization_recommendations (
        target_mission_id,
        source_mission_id,
        repository_point_id,
        clause_id,
        process_id,
        source_dimension,
        clause_vigilance_score,
        process_vigilance_score,
        retained_score,
        generated_priority_level_id,
        recommendation_label,
        prioritization_reason,
        generation_batch_code
    )
    SELECT
        v_target_mission_id,
        v_source_mission_id,
        pwr.repository_point_id,
        pwr.clause_id,
        pwr.process_id,
        pwr.source_dimension,
        pwr.clause_vigilance_score,
        pwr.process_vigilance_score,
        pwr.retained_score,
        pl.priority_level_id,
        pwr.recommendation_label,
        CONCAT(
            pwr.explanation_template,
            ' Score retenu : ', pwr.retained_score, '/100.',
            CASE
                WHEN pwr.clause_vigilance_score IS NOT NULL THEN CONCAT(' Vigilance clause : ', pwr.clause_vigilance_score, '/100.')
                ELSE ''
            END,
            CASE
                WHEN pwr.process_vigilance_score IS NOT NULL THEN CONCAT(' Vigilance processus : ', pwr.process_vigilance_score, '/100.')
                ELSE ''
            END
        ),
        v_batch_code
    FROM points_with_rules pwr
    JOIN priority_levels pl ON pl.label = pwr.generated_priority_label;

    GET DIAGNOSTICS v_recommendations_count = ROW_COUNT;

    -- 6.5 Création de la checklist
    INSERT INTO checklists (
        mission_id,
        checklist_title,
        checklist_status,
        generated_at
    )
    VALUES (
        v_target_mission_id,
        v_checklist_title,
        'Brouillon',
        CURRENT_TIMESTAMP
    )
    RETURNING checklist_id INTO v_checklist_id;

    -- 6.6 Items de checklist
    INSERT INTO checklist_items (
        checklist_id,
        theme,
        requirement_text,
        question_text,
        expected_evidence,
        priority_level_id,
        display_order,
        is_manually_modified,
        clause_id,
        conformity_status,
        finding_comment,
        examined_evidence
    )
    SELECT
        v_checklist_id,
        COALESCE(ct.theme_name, 'Référentiel ISO 9001'),
        cpr.requirement_text,
        cpr.question_template,
        CONCAT(
            COALESCE(cpr.expected_evidence, 'Preuves à définir lors de la préparation.'),
            ' | Justification de priorisation : ',
            rec.prioritization_reason
        ),
        rec.generated_priority_level_id,
        ROW_NUMBER() OVER (
            ORDER BY
                rec.retained_score DESC,
                CASE pl.label
                    WHEN 'Haute' THEN 1
                    WHEN 'Moyenne' THEN 2
                    ELSE 3
                END,
                sc.clause_code NULLS LAST,
                cpr.repository_point_id
        ),
        FALSE,
        cpr.clause_id,
        'Non évalué',
        rec.recommendation_label,
        NULL
    FROM checklist_prioritization_recommendations rec
    JOIN control_points_repository cpr
        ON cpr.repository_point_id = rec.repository_point_id
    LEFT JOIN control_themes ct
        ON ct.theme_id = cpr.theme_id
    LEFT JOIN standard_clauses sc
        ON sc.clause_id = cpr.clause_id
    LEFT JOIN priority_levels pl
        ON pl.priority_level_id = rec.generated_priority_level_id
    WHERE rec.target_mission_id = v_target_mission_id
      AND rec.source_mission_id = v_source_mission_id
      AND rec.generation_batch_code = v_batch_code;

    GET DIAGNOSTICS v_checklist_items_count = ROW_COUNT;

    -- 6.7 Journalisation
    INSERT INTO smart_checklist_generation_runs (
        source_mission_id,
        target_mission_id,
        generation_batch_code,
        generated_checklist_title,
        clause_scores_recomputed,
        process_scores_recomputed,
        recommendations_count,
        checklist_items_count
    )
    VALUES (
        v_source_mission_id,
        v_target_mission_id,
        v_batch_code,
        v_checklist_title,
        TRUE,
        TRUE,
        v_recommendations_count,
        v_checklist_items_count
    );

    RETURN QUERY
    SELECT
        v_batch_code,
        v_checklist_title,
        v_recommendations_count,
        v_checklist_items_count;
END;
$$;


-- ============================================================
-- 7. VUES DYNAMIQUES MULTI-MISSION POUR LE DASHBOARD V3
-- ============================================================

CREATE OR REPLACE VIEW vw_dynamic_clause_vigilance_dashboard AS
SELECT
    am.mission_code AS source_mission_code,
    am.mission_title AS source_mission_title,
    COALESCE(sc.clause_code, 'Sans clause') AS clause_code,
    COALESCE(sc.clause_title, 'Aucune référence ISO enregistrée') AS clause_title,
    cvs.findings_count,
    cvs.nonconformities_count,
    cvs.remarks_count,
    cvs.improvements_count,
    cvs.open_corrective_actions_count,
    cvs.raw_score,
    cvs.capped_score,
    rl.label AS vigilance_level,
    cvs.explanation_summary,
    cvs.computed_at
FROM clause_vigilance_scores cvs
JOIN audit_missions am ON am.mission_id = cvs.mission_id
LEFT JOIN standard_clauses sc ON sc.clause_id = cvs.clause_id
LEFT JOIN risk_levels rl ON rl.risk_level_id = cvs.risk_level_id;


CREATE OR REPLACE VIEW vw_dynamic_process_vigilance_dashboard AS
SELECT
    am.mission_code AS source_mission_code,
    am.mission_title AS source_mission_title,
    COALESCE(p.process_name, 'Processus non renseigné') AS process_name,
    pvs.findings_count,
    pvs.nonconformities_count,
    pvs.remarks_count,
    pvs.improvements_count,
    pvs.open_corrective_actions_count,
    pvs.raw_score,
    pvs.capped_score,
    rl.label AS vigilance_level,
    pvs.explanation_summary,
    pvs.computed_at
FROM process_vigilance_scores pvs
JOIN audit_missions am ON am.mission_id = pvs.mission_id
LEFT JOIN processes p ON p.process_id = pvs.process_id
LEFT JOIN risk_levels rl ON rl.risk_level_id = pvs.risk_level_id;


CREATE OR REPLACE VIEW vw_dynamic_generation_runs AS
SELECT
    run.generation_run_id,
    run.generation_batch_code,
    run.generated_checklist_title,
    src.mission_code AS source_mission_code,
    src.mission_title AS source_mission_title,
    tgt.mission_code AS target_mission_code,
    tgt.mission_title AS target_mission_title,
    run.recommendations_count,
    run.checklist_items_count,
    run.generated_at
FROM smart_checklist_generation_runs run
JOIN audit_missions src ON src.mission_id = run.source_mission_id
JOIN audit_missions tgt ON tgt.mission_id = run.target_mission_id;


CREATE OR REPLACE VIEW vw_dynamic_smart_checklist_recommendations AS
SELECT
    run.generation_run_id,
    run.generation_batch_code,
    tgt.mission_code AS target_mission_code,
    tgt.mission_title AS target_mission_title,
    src.mission_code AS source_mission_code,
    src.mission_title AS source_mission_title,
    rec.recommendation_id,
    sc.clause_code,
    sc.clause_title,
    p.process_name,
    rec.source_dimension,
    cpr.question_template,
    rec.clause_vigilance_score,
    rec.process_vigilance_score,
    rec.retained_score,
    pl.label AS generated_priority,
    rec.recommendation_label,
    rec.prioritization_reason,
    rec.generated_at
FROM checklist_prioritization_recommendations rec
JOIN audit_missions tgt ON tgt.mission_id = rec.target_mission_id
JOIN audit_missions src ON src.mission_id = rec.source_mission_id
LEFT JOIN smart_checklist_generation_runs run
    ON run.generation_batch_code = rec.generation_batch_code
JOIN control_points_repository cpr
    ON cpr.repository_point_id = rec.repository_point_id
LEFT JOIN standard_clauses sc
    ON sc.clause_id = rec.clause_id
LEFT JOIN processes p
    ON p.process_id = rec.process_id
LEFT JOIN priority_levels pl
    ON pl.priority_level_id = rec.generated_priority_level_id;


CREATE OR REPLACE VIEW vw_dynamic_smart_checklist_items AS
SELECT
    run.generation_run_id,
    run.generation_batch_code,
    tgt.mission_code AS target_mission_code,
    tgt.mission_title AS target_mission_title,
    src.mission_code AS source_mission_code,
    src.mission_title AS source_mission_title,
    c.checklist_title,
    ci.display_order,
    sc.clause_code,
    sc.clause_title,
    ci.theme,
    ci.question_text,
    pl.label AS generated_priority,
    ci.finding_comment AS recommendation_label,
    ci.expected_evidence,
    ci.conformity_status
FROM smart_checklist_generation_runs run
JOIN audit_missions tgt
    ON tgt.mission_id = run.target_mission_id
JOIN audit_missions src
    ON src.mission_id = run.source_mission_id
JOIN checklists c
    ON c.mission_id = tgt.mission_id
   AND c.checklist_title = run.generated_checklist_title
JOIN checklist_items ci
    ON ci.checklist_id = c.checklist_id
LEFT JOIN standard_clauses sc
    ON sc.clause_id = ci.clause_id
LEFT JOIN priority_levels pl
    ON pl.priority_level_id = ci.priority_level_id;


CREATE OR REPLACE VIEW vw_dynamic_smart_checklist_kpi_by_priority AS
SELECT
    generation_batch_code,
    target_mission_code,
    source_mission_code,
    generated_priority,
    COUNT(*) AS questions_count
FROM vw_dynamic_smart_checklist_items
GROUP BY
    generation_batch_code,
    target_mission_code,
    source_mission_code,
    generated_priority;


CREATE OR REPLACE VIEW vw_dynamic_top_vigilance_alerts AS
SELECT
    source_mission_code,
    'Clause ISO' AS alert_dimension,
    clause_code AS alert_key,
    clause_title AS alert_label,
    capped_score,
    vigilance_level,
    explanation_summary
FROM vw_dynamic_clause_vigilance_dashboard

UNION ALL

SELECT
    source_mission_code,
    'Processus' AS alert_dimension,
    process_name AS alert_key,
    process_name AS alert_label,
    capped_score,
    vigilance_level,
    explanation_summary
FROM vw_dynamic_process_vigilance_dashboard;


-- ============================================================
-- 8. TESTS RECOMMANDÉS APRÈS EXÉCUTION DU V6
-- ============================================================

-- 8.1 Vérifier les missions historiques disponibles
-- SELECT *
-- FROM auditprep.vw_available_historical_missions;

-- 8.2 Vérifier les missions cibles disponibles
-- SELECT *
-- FROM auditprep.vw_available_target_missions;

-- 8.3 Générer une checklist dynamique avec les missions déjà testées
-- SELECT *
-- FROM auditprep.fn_generate_smart_checklist(
--     'AUD-SECURE-2026-RENOUV',
--     'AUD-XYZ-2026-INT'
-- );

-- 8.4 Vérifier le run généré
-- SELECT *
-- FROM auditprep.vw_dynamic_generation_runs
-- ORDER BY generated_at DESC;

-- 8.5 Vérifier les KPI dynamiques de la checklist générée
-- SELECT *
-- FROM auditprep.vw_dynamic_smart_checklist_kpi_by_priority
-- ORDER BY generation_batch_code, generated_priority;

-- ============================================================
-- FIN DU SCRIPT SQL V6
-- ============================================================
