/// One hub tool as described by GET /api/v1/tools (same catalogue the MCP server publishes).
class ToolSpec {
  ToolSpec({
    required this.name,
    required this.description,
    required this.properties,
    required this.required,
    required this.readOnly,
    required this.destructive,
  });

  final String name;
  final String description;
  final Map<String, Map<String, dynamic>> properties;
  final List<String> required;
  final bool readOnly;
  final bool destructive;

  factory ToolSpec.fromJson(Map<String, dynamic> j) {
    final schema = (j['inputSchema'] as Map?)?.cast<String, dynamic>() ?? const {};
    final props = <String, Map<String, dynamic>>{};
    ((schema['properties'] as Map?) ?? const {}).forEach((k, v) => props[k as String] = (v as Map).cast<String, dynamic>());
    final ann = (j['annotations'] as Map?)?.cast<String, dynamic>() ?? const {};
    return ToolSpec(
      name: j['name'] as String,
      description: (j['description'] as String?) ?? '',
      properties: props,
      required: ((schema['required'] as List?) ?? const []).cast<String>(),
      readOnly: ann['readOnlyHint'] == true,
      destructive: ann['destructiveHint'] == true,
    );
  }

  /// "send_hr_emails" -> "Send hr emails"
  String get title {
    final s = name.replaceAll('_', ' ');
    return s[0].toUpperCase() + s.substring(1);
  }

  String get category => toolCategory(name);
}

const _categories = <String, List<String>>{
  'Discover jobs': ['search_jobs', 'multi_board_search', 'job_detail', 'list_jobs', 'scan_hiring_posts', 'hiring_post_queries'],
  'Apply': ['apply_jobs', 'login_platform', 'prepare_linkedin', 'tailor_application', 'verify_resume_pdf', 'guard_job_posting'],
  'Autopilot': ['autopilot_run', 'autopilot_stop', 'run_status', 'cancel_run'],
  'Find contacts': [
    'find_contact', 'find_hr_emails', 'find_company_emails', 'lead_finder_search', 'mailscout_find_emails',
    'verdict_email_tool', 'verify_email', 'add_hr_contact', 'list_hr_contacts', 'suppress_contact',
  ],
  'Outreach': ['compose_email', 'send_email', 'send_hr_emails', 'generate_linkedin_pitch'],
  'Status': ['hub_status', 'tracker_report'],
};

String toolCategory(String name) {
  for (final e in _categories.entries) {
    if (e.value.contains(name)) return e.key;
  }
  return 'Other';
}

const categoryOrder = ['Status', 'Discover jobs', 'Apply', 'Autopilot', 'Find contacts', 'Outreach', 'Other'];
