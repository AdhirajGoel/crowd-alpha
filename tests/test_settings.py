import settings
config={'engine':'codex','executable':'codex','model':'gpt-6-astra','timezone':'America/Los_Angeles',
        'installed_date':'2026-10-30','accounts':{'1':'demo_source_1'},'x_monthly_usd':1,'x_resource_usd':0.01}
assert settings.validate(config)==config
for update in ({'accounts':{}},{'accounts':{'1':'bad) OR from:someone'}},{'x_monthly_usd':float('nan')},
               {'x_resource_usd':-1},{'hour':24},{'engine':'other'},{'model':''}):
    try:settings.validate(dict(config,**update))
    except (ValueError,KeyError):pass
    else:raise AssertionError(update)
print('PASS: configuration trust boundaries')
