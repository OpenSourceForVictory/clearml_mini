import {test,expect} from '@playwright/test';
for(const admin of [true,false])test('resource role views and shared device counting: admin='+admin,async({page})=>{
 const slots=[['c',['client']],['s',['server']],['both',['client','server']]].map(([name,roles])=>({id:name,node_name:name,roles,gpu_index:0,online:true,gpu_model:'GPU',vram_mb:1024,active_tasks:0,reserved_vram_mb:0}));
 await page.addInitScript(()=>sessionStorage.setItem('eval-token','test'));
 await page.route('**/api/v1/**',async route=>{
  const url=new URL(route.request().url()),p=url.pathname.replace('/api/v1/','');let data:any=[];
  if(p==='auth/me')data={username:admin?'admin':'alice',role:admin?'admin':'user'};
  if(p==='workers'){
   const role=url.searchParams.get('role'),visible=slots.filter(s=>!role||(s.roles as string[]).includes(role));
   data=admin?visible:{total:visible.length,online:visible.length};
  }
  await route.fulfill({json:data});
 });
 await page.goto('/');await page.getByRole('button',{name:'计算资源',exact:true}).click();
 await expect(page.locator('.stats').getByText('3',{exact:true})).toHaveCount(2);
 await page.getByRole('tab',{name:'服务端资源',exact:true}).click();await expect(page.locator('.stats').getByText('2',{exact:true})).toHaveCount(2);
 if(admin){await expect(page.getByRole('cell',{name:'s / 0',exact:true})).toBeVisible();await expect(page.getByRole('cell',{name:'c / 0',exact:true})).toHaveCount(0);await expect(page.getByRole('cell',{name:'both / 0',exact:true})).toBeVisible();}
 else await expect(page.getByRole('table')).toHaveCount(0);
 await page.getByRole('tab',{name:'客户端资源',exact:true}).click();
 if(admin){await expect(page.getByRole('cell',{name:'c / 0',exact:true})).toBeVisible();await expect(page.getByRole('cell',{name:'s / 0',exact:true})).toHaveCount(0);}
 await page.getByRole('tab',{name:'全部资源',exact:true}).click();await expect(page.locator('.stats').getByText('3',{exact:true})).toHaveCount(2);
});
