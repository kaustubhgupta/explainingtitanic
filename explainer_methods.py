import numpy as np
import pandas as pd

import shap
from dtreeviz.trees import ShadowDecTree

from sklearn.metrics import classification_report, roc_auc_score
from sklearn.base import clone
from sklearn.model_selection import train_test_split, StratifiedKFold

def get_feature_dict(all_cols, cats=None):
    """ 
    feature_dict is a dictionary with all the columns belonging to 
    each onehotencoded feature:
    e.g. {'Gender' : ['Gender_Male', 'Gender_Female']}
    """
    
    feature_dict = {}
    
    if cats is None: 
        return {col:[col] for col in all_cols}

    for col in cats:
        cat_cols = [c for c in all_cols if c.startswith(col)]
        if len(cat_cols) > 1:
            feature_dict[col] = cat_cols

    # add all the individual features
    other_cols = list(
            # individual features = set of all columns minus the onehot columns
            set(all_cols)
             - set([item for sublist in list(feature_dict.values()) 
                                for item in sublist]))
    
    for col in other_cols:
        feature_dict[col] = [col]
    return feature_dict


def retrieve_onehot_value(X, encoded_col):
    """
    Returns a pd.Series with the original values that were onehot encoded
    """
    cat_cols = [c for c in X.columns if c.startswith(encoded_col+'_')]
    
    assert len(cat_cols) > 0, \
        f"No columns that start with {encoded_col} in DataFrame"
        
    feature_value = np.argmax(X[cat_cols].values, axis=1)
    
    # if not a single 1 then encoded feature must have been dropped
    feature_value[np.max(X[cat_cols].values, axis=1)==0]=-1 
    mapping = {-1: "NOT_ENCODED"}
    mapping.update({i: col[len(encoded_col):] for i, col in enumerate(cat_cols)})
    
    return pd.Series(feature_value).map(mapping)


def merge_categorical_shap_values(X, shap_values, cats=None):
    """ 
    Returns a new feature Dataframe X_cats and new shap values DataFrame shap_df
    where the shap values of onehotencoded categorical features have been 
    summed up.
    """ 
    feature_dict = get_feature_dict(X.columns, cats)
    shap_df = pd.DataFrame(shap_values, columns=X.columns)
    X_cats = X.copy()
    
    for col_name, col_list in feature_dict.items():
        if len(col_list) > 1:
            shap_df[col_name]=shap_df[col_list].sum(axis=1)
            shap_df.drop(col_list, axis=1, inplace=True)
            
            X_cats[col_name]=retrieve_onehot_value(X, col_name)
            X_cats.drop(col_list, axis=1, inplace=True)
    
    return X_cats, shap_df.values


def merge_categorical_shap_interaction_values(
            old_columns, new_columns, shap_interaction_values):
    """ 
    Returns a 3d numpy array shap_interaction_values where the categorical 
    columns have been summed up.
    
    Caution:
    Column names in new_columns that are not found in old_columns are 
    assumed to be categorical feature names.
    
    """ 
    
    if isinstance(old_columns, pd.DataFrame): 
        old_columns = old_columns.columns.tolist()
    if isinstance(new_columns, pd.DataFrame): 
        new_columns = new_columns.columns.tolist()
        
    if not isinstance(old_columns, list) : old_columns = old_columns.tolist()
    if not isinstance(new_columns, list) : new_columns = new_columns.tolist()
        

    cats = [col for col in new_columns if col not in old_columns]
    feature_dict = get_feature_dict(old_columns, cats)
    
    siv = np.zeros((shap_interaction_values.shape[0], 
                    len(new_columns), 
                    len(new_columns)))
    
    for new_col1 in new_columns:
        for new_col2 in new_columns:
            newcol_idx1 = new_columns.index(new_col1)
            newcol_idx2 = new_columns.index(new_col2)
            oldcol_idxs1 = [old_columns.index(col) 
                                for col in feature_dict[new_col1]]
            oldcol_idxs2 = [old_columns.index(col) 
                                for col in feature_dict[new_col2]]
            siv[:, newcol_idx1, newcol_idx2] = \
                shap_interaction_values[:, oldcol_idxs1, :][:, :, oldcol_idxs2]\
                .sum(axis=(1,2))
            
    return siv


def permutation_importances(model, X, y, metric, cats=None, 
                            greater_is_better=True, needs_proba=True, 
                            sort=True, verbose=0):
    """
    adapted from rfpimp
    """
    X = X.copy()
    
    feature_dict = get_feature_dict(X.columns, cats)

    if isinstance(metric, str):
        scorer = make_scorer(metric, greater_is_better, needs_proba)
    else:
        scorer = metric
    
    if needs_proba:
        y_pred = model.predict_proba(X)
        baseline = scorer(y, y_pred[:,1])
    else:
        y_pred = model.predict(X)
        baseline = scorer(y, y_pred)
    
    if verbose:
        print('baseline: ', baseline)
        
    imp = pd.DataFrame({'Importance':[]})
    
        
    for col_name, col_list in feature_dict.items():
        old_cols = X[col_list].copy()
        X[col_list] = np.random.permutation(X[col_list])
        
        if needs_proba:
            y_pred = model.predict_proba(X)
            permutation_score = scorer(y, y_pred[:,1])
        else:
            y_pred = model.predict(X)
            permutation_score = scorer(y, y_pred)

        drop_in_metric = baseline - permutation_score
        imp = imp.append(pd.DataFrame({'Importance':[drop_in_metric]}, index=[col_name]))
        X[col_list] = old_cols
    
    imp.index.name = 'Feature'
    if sort:
        return imp.sort_values('Importance', ascending=False)
    else:
        return imp


def cv_permutation_importances(model, X, y, metric, cats=None, greater_is_better=True, 
                                needs_proba=True, cv=5, verbose=0):
    """
    Returns the permutation importances averages over `cv` cross-validated folds.
    """ 
    skf = StratifiedKFold(n_splits=cv, random_state=None, shuffle=False)
    model = clone(model)
    for i, (train_index, test_index) in enumerate(skf.split(X, y)):
        X_train, X_test = X.iloc[train_index], X.iloc[test_index]
        y_train, y_test = y.iloc[train_index], y.iloc[test_index]
        
        model.fit(X_train, y_train)
        
        imp = permutation_importances(model, X_test, y_test, metric, cats,
                                        greater_is_better=greater_is_better, 
                                        needs_proba=needs_proba, 
                                        sort=False, 
                                        verbose=verbose)
        
        if i==0:
            imps = imp
        else:
            imps = imps.merge(imp, on='Feature', suffixes=("", "_"+str(i)))
        
    return pd.DataFrame(imps.mean(axis=1), columns=['Importance'])\
                        .sort_values('Importance', ascending=False)


def mean_absolute_shap_values(columns, shap_values, cats=None):
    """ 
    Returns a dataframe with the mean absolute shap values for each feature.
    """ 
    feature_dict = get_feature_dict(columns, cats)
    
    shap_abs_mean_dict = {}
    for col_name, col_list in feature_dict.items():
        shap_abs_mean_dict[col_name] = np.absolute(
            shap_values[:, [columns.index(col) for col in col_list]].sum(axis=1)).mean()
        
    shap_df = pd.DataFrame({'Feature': list(shap_abs_mean_dict.keys()), 
                  'MEAN_ABS_SHAP': list(shap_abs_mean_dict.values())})\
                    .sort_values('MEAN_ABS_SHAP', ascending=False)\
                    .reset_index(drop=True)
    
    return shap_df


def get_precision_df(pred_probas, y_true, bin_size):
    """
    returns a pd.DataFrame with the predicted probabilities and 
    the observed frequency per bin_size. 
    """
    
    if len(pred_probas.shape)==2:
        # in case the full binary classifier pred_proba is passed, 
        # we only select the probability of the positive class
        pred_probas = pred_probas[:,1]

    predictions_df = pd.DataFrame({'pred_proba': pred_probas,'target': y_true})

    # define a placeholder df:
    precision_df = pd.DataFrame(columns = ['pred_proba', 'avg_target', 'count'])

    thresholds = np.arange(0, 1.0, bin_size).tolist()
    # loop through prediction intervals, and compute
    for bin_min, bin_max in zip(thresholds, thresholds[1:] + [1.0]):
        avg_target = predictions_df[(predictions_df.pred_proba> bin_min) & 
                                  (predictions_df.pred_proba<= bin_max)].target.mean()
        bin_count = predictions_df[(predictions_df.pred_proba> bin_min) & 
                                    (predictions_df.pred_proba<= bin_max)].target.count()
        new_row = pd.DataFrame(
            {
                'pred_proba' : [(bin_min+bin_max)/2],
                'avg_target' : [avg_target],
                'count' : [bin_count]
            })
        precision_df = pd.concat([precision_df, new_row])
    return precision_df
            

def get_contrib_df(base_value, contributions, feature_names, raw_values=None, 
                    cats=None, topx=None, cutoff=None):
    """
    Return a contrib_df DataFrame that lists the contribution of each input
    variable.

    if raw_values is given, include the raw_values.
    If cats is given, adds up the contributions for each categorical variable.
    if topx is given, only returns the highest topx contributions
    if cutoff is given only returns contributions above cutoff.
    """    

    contrib_df = pd.DataFrame(
                    {
                        'col': feature_names, 
                        'contribution': contributions
                    })

    # For onehot encoded categorical variables, we add up all individual
    # contributions:
    if cats is not None:
        for cat_col in cats:
            cols = contrib_df[contrib_df.col.str.startswith(cat_col)].col.values.tolist()
            # sum up contributions of all categories in cat
            if cols:
                cumsum = contrib_df[contrib_df.col.str.startswith(cat_col)].contribution.sum()
                # append this under original cat col name
                contrib_df = contrib_df.append(
                                pd.DataFrame(
                                    {'col':[cat_col], 'contribution':[cumsum]}))
                # drop all the onehot encoded columns:
                contrib_df = contrib_df[~contrib_df.col.isin(cols)]

    # sort the df by absolute value from highest to lowest:  
    contrib_df = contrib_df.reset_index(drop=True)
    contrib_df = contrib_df.reindex(
                    contrib_df.contribution.abs()\
                                .sort_values(ascending=False).index)

    
    base_df = pd.DataFrame({'col':['base_value'], 
                            'contribution':[base_value]})


    contrib_df = pd.concat([base_df, contrib_df], ignore_index=True)


    # add cumulative contribution from top to bottom (for making graph):
    contrib_df['cumulative'] = contrib_df.contribution.cumsum()
    
    # if a cutoff is given for minimum contribution to be displayed, calculate what topx rows to return:
    if cutoff is not None:
        cutoff = contrib_df.contribution[np.abs(contrib_df.contribution)>=cutoff].index.max()+1
        if topx is not None and cutoff < topx:
            topx = cutoff

    # if only returning topx columns, sum the remainder contributions under 'REST'
    if topx is not None:
        if topx > len(contrib_df): topx = len(contrib_df)
        old_cum = contrib_df.iloc[[topx-1]].cumulative.item()
        tot_cum = contrib_df.iloc[[-1]].cumulative.item()
        diff = tot_cum-old_cum

        rest_df = pd.DataFrame({'col':['REST'], 'contribution':[diff], 'cumulative':[tot_cum]})

        contrib_df = pd.concat([contrib_df.head(topx), rest_df], axis=0).reset_index(drop=True)
    
    # add the cumulative before the current variable (i.e. the base of the
    # bar in the graph):
    contrib_df['base']= contrib_df['cumulative'] - contrib_df['contribution']

    # add raw values:
    if raw_values is not None:
        contrib_df = contrib_df.merge(raw_values.rename('raw_value'), 
                                        left_on='col', right_index=True, how='left')

    # round the final numbers:
#         contrib_df['contribution'] = np.round(contrib_df['contribution'], 2)
#         contrib_df['cumulative'] = np.round(contrib_df['cumulative'], 2)
#         contrib_df['base'] = np.round(contrib_df['base'], 2)
    return contrib_df


def get_contrib_summary_df(contrib_df):
    """ 
    returns a DataFrame that summarizes a contrib_df as a pair of 
    Reasons+Effect. 
    """ 
    contrib_summary_df = pd.DataFrame(columns=['Reason', 'Effect'])

    for idx, row in contrib_df.iterrows():
        if row['col'] != 'base_value':
            contrib_summary_df = contrib_summary_df.append(
                    pd.DataFrame({
                        'Reason': [f"{row['col']} = {row['raw_value']}"],
                        'Effect': [f"{'+' if row['contribution'] >= 0 else ''}{np.round(100*row['contribution'], 1)}%"]
                    }))     
    return contrib_summary_df.reset_index(drop=True)


def normalize_shap_interaction_values(shap_interaction_values, shap_values=None):
    """
    Normalizes shap_interaction_values to make sure that the rows add up to 
    the shap_values.
    
    This is a workaround for an apparant bug where the diagonals of 
    shap_interaction_values of a RandomForestClassifier are set equal to the 
    shap_values instead of the main effect. 
    """
    siv = shap_interaction_values.copy()
    
    orig_diags = np.einsum('ijj->ij', siv)
    row_sums = np.einsum('ijk->ij', siv)
    row_diffs = row_sums - orig_diags # sum of rows excluding diagonal elements
    
    if shap_values is not None:
        diags = shap_values - row_diffs
    else:
        # if no shap_values provided assume that the original diagonal values 
        # were indeed equal to the shap values, and so simply 
        diags = orig_diags - row_diffs
    
    s0,s1,s2 = siv.shape
    siv.reshape(s0,-1)[:,::s2+1] = diags
    return siv


def get_shadow_trees(rf_model, X, y):
    """
    Returns a list of ShadowDecTree from the dtreeviz package
    
    """
    shadow_trees = [ShadowDecTree(decision_tree, 
                                  X, 
                                  y, 
                                  feature_names=X.columns.tolist(),
                                  class_names = ['Neg', 'Pos']) 
                        for decision_tree in rf_model.estimators_]
    return shadow_trees


def get_shadowtree_df(shadow_tree, observation):
    pred, nodes = shadow_tree.predict(observation)
    
    shadowtree_df = pd.DataFrame(columns=['node_id', 'average', 'feature', 
                                     'value', 'split', 'direction', 
                                     'left', 'right'])
    if shadow_tree.isclassifier()[0]:
        def node_pred_proba(node):
            return node.class_counts()[1]/ (node.class_counts()[0] + node.class_counts()[1])
        for node in nodes:
            if not node.isleaf():
                shadowtree_df = shadowtree_df.append({
                    'node_id' : node.id,
                    'average' : node_pred_proba(node),
                    'feature' : node.feature_name(),
                    'value' : observation[node.feature_name()], 
                    'split' : node.split(), 
                    'direction' : 'left' if observation[node.feature_name()] < node.split() else 'right',
                    'left' : node_pred_proba(node.left),
                    'right' : node_pred_proba(node.right),
                    'diff' : node_pred_proba(node.left) - node_pred_proba(node) \
                                if observation[node.feature_name()] < node.split() \
                                else node_pred_proba(node.right) - node_pred_proba(node) 
                }, ignore_index=True)
        
    else:
        for node in nodes:
            if not node.isleaf():
                shadowtree_df = shadowtree_df.append({
                    'node_id' : node.id,
                    'average' : np.mean(shadow_tree.y_train[node.samples()]),
                    'feature' : node.feature_name(),
                    'value' : observation[node.feature_name()], 
                    'split' : node.split(), 
                    'direction' : 'left' if observation[node.feature_name()] < node.split() else 'right',
                    'left' : np.mean(shadow_tree.y_train[node.left.samples()]),
                    'right' : np.mean(shadow_tree.y_train[node.right.samples()]),
                }, ignore_index=True)
            
    return shadowtree_df


def shadowtree_df_summary(shadow_df):
    base_value = np.round(100*shadow_df.iloc[[0]]['average'].item(), 2)
    prediction = np.round(100*(shadow_df.iloc[[-1]]['average'].item() + shadow_df.iloc[[-1]]['diff'].item()), 2)
    
    shadow_summary_df = pd.DataFrame(columns=['value', 'condition', 'change', 'prediction'])
    
    for index, row in shadow_df.iterrows():
        shadow_summary_df = shadow_summary_df.append({
                        'value' : (str(row['feature'])+'='+str(row['value'])).ljust(50),
                        'condition' : str('>=' if row['direction'] == 'right' else '< ') + str(row['split']).ljust(10),
                        'change' : str('+' if row['diff'] >= 0 else '') + str(np.round(100*row['diff'], 2)) +'%',
                        'prediction' : str(np.round(100*(row['average']+row['diff']),2)) + '%'
                    }, ignore_index=True)
    return base_value, prediction, shadow_summary_df