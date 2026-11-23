"use strict";

const TOUR_STOPS = [
  {
    page: "upload", target: ".steps", title: "Follow a dataset from file to findings",
    description: "Upload a file, choose its checks, then open the report. This tour uses sample records. Select Next to explore or Skip tour to return to your work."
  },
  {
    page: "upload", target: "#dropzone", title: "Start with the responses",
    description: "Drop your evaluation file here or select Choose file. Use the file containing the AI responses and their ratings. You can add the scoring rubric separately."
  },
  {
    page: "checks", target: ".rubric-card > .rubric-actions", title: "Match your rubric to score columns",
    description: "Choose a rubric file to bring in its criteria and scales. Check the proposed column for each criterion, change any mismatches, then select Apply rubric."
  },
  {
    page: "checks", target: "#rating-rules", title: "Define a valid rating",
    description: "For each score column, choose whether a value is required, whether it must be a whole number, and its allowed range. With a range of 1 to 5, a rating of 7 becomes a finding."
  },
  {
    page: "checks", target: ".audit-settings > .section-heading", title: "Check patterns across records",
    description: "Include audit to find repeated answers to the same prompt or conflicting ratings for the same answer. To look for gaps, choose a category column and the minimum number of examples you expect. Select Run inspection when ready."
  },
  {
    page: "report", target: ".report-heading", title: "Read and save the inspection",
    description: "Simple shows the inspection outcome and highlights. Choose Detailed to explore individual findings and score distributions. Download report bundle saves both views in one HTML file alongside the data."
  },
  {
    page: "report", target: "#findings-wrap", title: "See exactly what was flagged",
    description: "Record 3 scored 7 on a 1–5 scale. Record 4 is missing its response. Each row shows the value and the rule it failed. Open Records to review for the original prompt and answer."
  },
  {
    page: "report", target: "#semantic-title", title: "Check whether a rating fits the answer",
    description: "Choose the whole dataset or selected records, pick a rubric and model, and estimate the cost locally. You can review the price and scope before confirming a paid review."
  },
  {
    page: "upload", target: ".single-task-entry", title: "Review a single response",
    description: "For one training task, choose Review one task from the main page. This path starts with the prompt, the response, and your rubric."
  },
  {
    page: "single-task", target: ".single-task-text", title: "Put the answer beside its prompt",
    description: "Paste the instructions the AI received in Prompt and its actual reply in AI response. If scoring depends on a policy or expected answer, add it under Reference answer or evidence."
  },
  {
    page: "single-task", target: "#single-rubric-criteria", title: "Give each criterion a scale",
    description: "Upload your rubric, then expand a criterion to read or edit what each score means. CSV and JSON can describe several criteria. A text or Markdown file can describe one."
  },
  {
    page: "single-task", target: "#single-preview", title: "Preview before spending credits",
    description: "Choose the review model and select Preview evaluation to check the setup locally. When you confirm Run evaluation, the selected model assesses the response against each criterion using your API credits."
  },
  {
    page: "settings", target: "#key-settings-card", title: "Choose how to use your API key",
    description: "Save an OpenAI API key here to reuse it from your Mac’s Keychain. You can replace or remove it later, or use a one-time key when you run a review."
  },
  {
    page: "walkthrough", target: "#guide-task-title", title: "Revisit a topic whenever you need it",
    description: "Open Walkthrough in the header and expand the topic you need. It covers dataset checks, score explanations, second opinions, and downloads. Finish tour returns you to the page where you started."
  }
];
