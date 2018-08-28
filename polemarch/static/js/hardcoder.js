
 
tabSignal.connect("openapi.factory.setowner", function(data)
{ 
    let filed = apisetowner.one.view.definition.properties.user_id;
    filed.type = "select2"
    filed.search = function(params, filed, filed_value, parent_object)
    {
        let def = new $.Deferred(); 
        let list = new apiuser.list()
 
        $.when(list.search()).done((rawdata) =>
        { 
            if(!rawdata || !rawdata.data || !rawdata.data.results)
            {
                def.reject()
                return;
            }

            let results = []
            for(let i in rawdata.data.results)
            {
                results.push({id:rawdata.data.results[i].id, text:rawdata.data.results[i][list.parent.getObjectNameFiled()]})
            }

            def.resolve({results:results})
        }).fail(() => {
            def.reject()
        })
 
        return def.promise();
    };
    
})
  
// Исключения харкод для назвпний в апи
tabSignal.connect("openapi.factory.ansiblemodule", function(data)
{
    //let inventory = apiansiblemodule.one.view.definition.properties.inventory;
    //inventory.type = "autocomplete"
    //inventory.searchObj = new apiinventory.list();

    //let inventory = apiansiblemodule.one.view.definition.properties.inventory;
    //inventory.type = "select2"
    //inventory.searchObj = function(){
    //    return new apiinventory.list();
    //};

    let filed = apiansiblemodule.one.view.definition.properties.inventory;
    filed.type = "select2"
    filed.search = function(params, filed, filed_value, parent_object)
    {
        let def = new $.Deferred(); 
        let list = new apiinventory.list({api:api.openapi.paths["/project/{pk}/inventory/"]})
 
        $.when(list.search(spajs.urlInfo.data.reg)).done((rawdata) =>
        { 
            if(!rawdata || !rawdata.data || !rawdata.data.results)
            {
                def.reject()
                return;
            }

            let results = []
            for(let i in rawdata.data.results)
            {
                results.push({id:rawdata.data.results[i].id, text:rawdata.data.results[i][list.parent.getObjectNameFiled()]})
            }

            def.resolve({results:results})
        }).fail(() => {
            def.reject()
        })
 
        return def.promise();
    };
})
