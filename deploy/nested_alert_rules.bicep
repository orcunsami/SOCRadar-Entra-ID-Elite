param WorkspaceName string
param Enabled bool

resource leakRule 'Microsoft.OperationalInsights/workspaces/providers/alertRules@2024-03-01' = {
  name: '${WorkspaceName}/Microsoft.SecurityInsights/${guid(WorkspaceName, 'elite-leaked-credential-rule')}'
  kind: 'Scheduled'
  properties: {
    displayName: 'SOCRadar Elite - Leaked employee credential'
    description: 'Identifies a leaked credential matched to an employee account. Review sign-in activity, revoke sessions and reset the credential as appropriate.'
    severity: 'High'
    enabled: Enabled
    queryFrequency: 'PT1H'
    queryPeriod: 'P7D'
    triggerOperator: 'GreaterThan'
    triggerThreshold: 0
    suppressionDuration: 'PT1H'
    suppressionEnabled: false
    tactics: ['InitialAccess']
    techniques: ['T1078']
    eventGroupingSettings: { aggregationKind: 'AlertPerResult' }
    alertDetailsOverride: {
      alertDisplayNameFormat: 'SOCRadar Elite: Leaked credential ({{SourceName}})'
      alertDescriptionFormat: 'Leaked employee credential for {{AccountDisplay}} in company {{CompanyId}}. Source: {{SourceName}}. Review sign-ins and reset the credential.'
      alertSeverityColumnName: 'SentinelSeverity'
    }
    customDetails: {
      EliteDedupKey: 'DedupKey'
      CompanyId: 'CompanyId'
      Source: 'SourceName'
      EntraStatus: 'EntraStatus'
    }
    entityMappings: [{
      entityType: 'Account'
      fieldMappings: [
        { identifier: 'Name', columnName: 'AccountName' }
        { identifier: 'UPNSuffix', columnName: 'UPNSuffix' }
      ]
    }]
    incidentConfiguration: {
      createIncident: true
      groupingConfiguration: {
        enabled: false
        reopenClosedIncident: false
        lookbackDuration: 'PT5H'
        matchingMethod: 'AllEntities'
        groupByEntities: []
        groupByAlertDetails: []
        groupByCustomDetails: []
      }
    }
    query: '''
// The engine bounds all reads to queryPeriod: dedup lasts seven days, not forever.
// Hold fresh rows twelve minutes so the preceding run's alerts can be ingested.
let Alerted = union isfuzzy=true (datatable(TimeGenerated:datetime, ExtendedProperties:string)[]), SecurityAlert
| where TimeGenerated > ago(7d)
| extend AlertedKey = tostring(parse_json(tostring(parse_json(ExtendedProperties)['Custom Details'])).EliteDedupKey[0])
| where isnotempty(AlertedKey)
| distinct AlertedKey;
union
  (SOCRadar_Botnet_CL
   | project TimeGenerated, IngestedAt=ingestion_time(), company_id, email, is_employee, source, severity, entra_status,
       FindingId=tostring(pack_array(url, log_date, device_ip))),
  (SOCRadar_PII_CL
   | project TimeGenerated, IngestedAt=ingestion_time(), company_id, email, is_employee, source, severity, entra_status,
       FindingId=iff(alarm_id > 0, tostring(alarm_id), tostring(pack_array(source_name, breach_date, discovery_date)))),
  (SOCRadar_VIP_CL
   | project TimeGenerated, IngestedAt=ingestion_time(), company_id, email, is_employee, source, severity, entra_status,
       FindingId=iff(alarm_id > 0, tostring(alarm_id), tostring(pack_array(keyword, vip_name, source_name, discovery_date))))
| where IngestedAt > ago(7d) and IngestedAt < ago(12m)
| where is_employee == true and isnotempty(email) and isnotempty(company_id)
| where tolower(entra_status) in ('found', 'compromised')
| where email matches regex @"^[^@\s]+@[^@\s]+$"
| extend DedupKey = hash_sha256(tostring(pack_array(company_id, source, tolower(email), FindingId)))
| join kind=leftanti Alerted on $left.DedupKey == $right.AlertedKey
| summarize arg_max(TimeGenerated, *) by DedupKey
| extend AccountName=tostring(split(email, '@')[0]), UPNSuffix=tostring(split(email, '@')[1])
| extend AccountDisplay=email, CompanyId=company_id, SourceName=toupper(source), EntraStatus=entra_status
| extend SentinelSeverity=case(toupper(severity) in ('CRITICAL', 'HIGH'), 'High', toupper(severity) == 'LOW', 'Low', 'Medium')
| project TimeGenerated, DedupKey, AccountName, UPNSuffix, AccountDisplay, CompanyId, SourceName, EntraStatus, SentinelSeverity
| order by TimeGenerated asc
| take 149
'''
  }
}
